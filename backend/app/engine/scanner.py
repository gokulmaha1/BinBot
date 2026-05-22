"""
BinBot AI Auto Mode — Auto Pair Scanner
Continuously scans Binance Futures for the best tradeable pairs.

Responsibilities:
- Fetch all USDT-M futures pairs and 24h ticker data
- Apply hard filters (volume, listing age, spread, blacklist)
- Compute soft scoring (ATR volatility, volume spikes, OI change, ADX)
- Return top-ranked pairs with regime classification
- Cache results in Redis with 30s TTL
- Persist MarketSnapshot records to the database
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import numpy as np
from binance import AsyncClient

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_redis
from app.db.session import async_session_factory
from app.models import MarketSnapshot

logger = logging.getLogger("binbot.scanner")

# ── Constants ────────────────────────────────────────────────────
REDIS_KEY_SCANNER = "binbot:scanner"
SCANNER_CACHE_TTL = 30  # seconds
VOLUME_SPIKE_LOOKBACK = 20  # Periods for volume spike detection

# Blacklisted symbols — stablecoins, delisted, or illiquid pairs
SYMBOL_BLACKLIST: frozenset[str] = frozenset({
    "USDCUSDT",
    "BUSDUSDT",
    "TUSDUSDT",
    "FDUSDUSDT",
    "EURUSDT",
    "GBPUSDT",
    "BTCSTUSDT",
    "COCOSUSDT",
    "STRAXUSDT",
})


class PairScanner:
    """Automatic pair scanner that identifies the best futures to trade.

    Runs on a configurable interval (default 15s) and applies a two-phase
    filtering pipeline:

    1. **Hard filters** — absolute disqualifiers (volume, age, spread,
       blacklist).
    2. **Soft scoring** — ranked composite score from ATR volatility,
       volume spikes, open interest change, and ADX trend clarity.

    Results are cached in Redis and periodically written to the
    ``market_snapshots`` database table.
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._binance_client: Optional[AsyncClient] = None
        self._running: bool = False
        self._scan_task: Optional[asyncio.Task] = None
        self._last_scan_time: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise connections and start the background scan loop."""
        if self._running:
            logger.warning("PairScanner already running — ignoring start()")
            return

        logger.info("Starting PairScanner (interval=%ds)", settings.SCANNER_INTERVAL_SECONDS)

        self._redis = await get_redis()

        if settings.is_testnet:
            self._binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
                testnet=True,
            )
        else:
            self._binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
            )

        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info("PairScanner started")

    async def stop(self) -> None:
        """Gracefully shut down the scanner."""
        logger.info("Stopping PairScanner")
        self._running = False

        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        if self._binance_client:
            await self._binance_client.close_connection()
            self._binance_client = None

        logger.info("PairScanner stopped")

    # ── Background loop ──────────────────────────────────────────

    async def _scan_loop(self) -> None:
        """Periodic scan loop with error isolation."""
        while self._running:
            try:
                results = await self.scan()
                if results:
                    logger.info(
                        "Scan complete: %d pairs ranked (top: %s, score=%.1f)",
                        len(results),
                        results[0]["symbol"],
                        results[0]["score"],
                    )
                else:
                    logger.warning("Scan returned no qualifying pairs")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("Scan error: %s", exc, exc_info=True)

            await asyncio.sleep(settings.SCANNER_INTERVAL_SECONDS)

    # ── Core scan ────────────────────────────────────────────────

    async def scan(self) -> list[dict[str, Any]]:
        """Execute a full scan cycle.

        Steps:
            1. Fetch exchange info + 24h tickers + open interest
            2. Apply hard filters
            3. Fetch klines for qualifying pairs and compute scores
            4. Rank, cache, and persist results

        Returns:
            Top ``SCANNER_TOP_PAIRS`` ranked pairs as list of dicts.
        """
        if not self._binance_client:
            logger.error("Scanner not initialised — call start() first")
            return []

        start_ts = time.monotonic()

        # ── Step 1: Fetch raw data ───────────────────────────────
        try:
            exchange_info, tickers_24h = await asyncio.gather(
                self._binance_client.futures_exchange_info(),
                self._binance_client.futures_ticker(),
            )
        except Exception as exc:
            logger.error("Failed to fetch market data: %s", exc)
            return []

        # Build symbol info map
        symbol_info = self._parse_exchange_info(exchange_info)

        # Build ticker map
        ticker_map: dict[str, dict[str, Any]] = {}
        for t in tickers_24h:
            sym = t.get("symbol", "")
            if sym:
                ticker_map[sym] = t

        # ── Step 2: Hard filter ──────────────────────────────────
        candidates = self._apply_hard_filters(symbol_info, ticker_map)
        if not candidates:
            logger.warning("No candidates passed hard filters")
            return []

        logger.debug("%d candidates passed hard filters", len(candidates))

        # ── Step 3: Fetch klines + OI for scoring ────────────────
        scored_pairs = await self._score_candidates(candidates, ticker_map)

        # ── Step 4: Rank and truncate ────────────────────────────
        scored_pairs.sort(key=lambda x: x["score"], reverse=True)
        top_pairs = scored_pairs[: settings.SCANNER_TOP_PAIRS]

        # ── Step 5: Cache to Redis ───────────────────────────────
        await self._cache_results(top_pairs)

        # ── Step 6: Persist to database ──────────────────────────
        await self._persist_snapshots(top_pairs)

        elapsed = time.monotonic() - start_ts
        self._last_scan_time = elapsed
        logger.debug("Scan completed in %.2fs", elapsed)

        return top_pairs

    # ── Hard filters ─────────────────────────────────────────────

    def _parse_exchange_info(
        self, exchange_info: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Parse exchange info into a symbol → info mapping.

        Only includes perpetual USDT-margined contracts.
        """
        result: dict[str, dict[str, Any]] = {}
        now_ms = int(time.time() * 1000)

        for sym in exchange_info.get("symbols", []):
            symbol = sym.get("symbol", "")
            quote = sym.get("quoteAsset", "")
            contract_type = sym.get("contractType", "")
            status = sym.get("status", "")

            if (
                quote == "USDT"
                and contract_type == "PERPETUAL"
                and status == "TRADING"
            ):
                onboard_date = sym.get("onboardDate", now_ms)
                listing_age_days = (now_ms - onboard_date) / (86400 * 1000)

                # Extract tick/step sizes from filters
                price_precision = sym.get("pricePrecision", 2)
                qty_precision = sym.get("quantityPrecision", 3)

                result[symbol] = {
                    "symbol": symbol,
                    "listing_age_days": listing_age_days,
                    "price_precision": price_precision,
                    "qty_precision": qty_precision,
                    "filters": sym.get("filters", []),
                }

        return result

    def _apply_hard_filters(
        self,
        symbol_info: dict[str, dict[str, Any]],
        ticker_map: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Apply hard disqualifiers and return qualifying symbols.

        Filters:
            - 24h quote volume > ``SCANNER_MIN_VOLUME_24H``
            - Listing age > ``SCANNER_MIN_LISTING_DAYS``
            - Spread (ask−bid)/mid < ``SCANNER_MAX_SPREAD_PCT``
            - Not in ``SYMBOL_BLACKLIST``
        """
        passed: list[str] = []

        for symbol, info in symbol_info.items():
            # Blacklist check
            if symbol in SYMBOL_BLACKLIST:
                continue

            ticker = ticker_map.get(symbol)
            if not ticker:
                continue

            # Volume filter
            quote_volume = float(ticker.get("quoteVolume", 0))
            if quote_volume < settings.SCANNER_MIN_VOLUME_24H:
                continue

            # Listing age filter
            if info["listing_age_days"] < settings.SCANNER_MIN_LISTING_DAYS:
                continue

            # Spread filter
            ask = float(ticker.get("askPrice", 0))
            bid = float(ticker.get("bidPrice", 0))
            if ask <= 0 or bid <= 0:
                continue

            mid = (ask + bid) / 2
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
            if spread_pct > settings.SCANNER_MAX_SPREAD_PCT:
                continue

            passed.append(symbol)

        return passed

    # ── Soft scoring ─────────────────────────────────────────────

    async def _score_candidates(
        self,
        candidates: list[str],
        ticker_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compute composite scores for all candidate symbols.

        Scoring components (each normalised 0–100):
            - **ATR volatility** (30%): Higher ATR relative to price → better
            - **Volume spike** (25%): Current volume vs 20-period average
            - **OI change** (20%): Open interest change indicates new money
            - **ADX trend clarity** (25%): ADX > 25 → trending

        Returns:
            List of scored pair dicts.
        """
        # Batch fetch klines + OI concurrently
        tasks = []
        for sym in candidates:
            tasks.append(self._compute_pair_metrics(sym, ticker_map.get(sym, {})))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Scoring error: %s", result)
                continue
            if result is not None:
                scored.append(result)

        return scored

    async def _compute_pair_metrics(
        self,
        symbol: str,
        ticker: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Compute all scoring metrics for a single pair.

        Args:
            symbol: Trading pair.
            ticker: 24h ticker data.

        Returns:
            Scored pair dict or None if data is insufficient.
        """
        if not self._binance_client:
            return None

        try:
            # Fetch 1h klines for ATR/ADX calculation (need ~50 candles)
            klines_raw = await self._binance_client.futures_klines(
                symbol=symbol,
                interval="1h",
                limit=60,
            )

            if len(klines_raw) < 30:
                return None

            # Parse klines into numpy arrays
            highs = np.array([float(k[2]) for k in klines_raw], dtype=np.float64)
            lows = np.array([float(k[3]) for k in klines_raw], dtype=np.float64)
            closes = np.array([float(k[4]) for k in klines_raw], dtype=np.float64)
            volumes = np.array([float(k[7]) for k in klines_raw], dtype=np.float64)  # quote volume

            current_price = closes[-1]
            if current_price <= 0:
                return None

            # ── ATR calculation ──────────────────────────────────
            atr = self._calculate_atr(highs, lows, closes, settings.ATR_PERIOD)
            atr_pct = (atr / current_price) * 100  # ATR as % of price

            # ── ADX calculation ──────────────────────────────────
            adx = self._calculate_adx(highs, lows, closes, settings.ADX_PERIOD)

            # ── Volume spike detection ───────────────────────────
            recent_vol = volumes[-1]
            avg_vol = np.mean(volumes[-VOLUME_SPIKE_LOOKBACK:])
            vol_spike_ratio = (recent_vol / avg_vol) if avg_vol > 0 else 1.0

            # ── OI change ────────────────────────────────────────
            oi_change = await self._get_oi_change(symbol)

            # ── Regime classification ────────────────────────────
            regime = self._classify_regime(adx, atr_pct, vol_spike_ratio)

            # ── Composite score ──────────────────────────────────
            atr_score = min(atr_pct * 20, 100)  # 5% ATR → 100
            vol_score = min((vol_spike_ratio - 1) * 50, 100) if vol_spike_ratio > 1 else 0
            adx_score = min((adx / 50) * 100, 100) if adx > 25 else (adx / 25) * 40
            oi_score = min(abs(oi_change) * 10, 100)

            composite = (
                atr_score * 0.30
                + vol_score * 0.25
                + oi_score * 0.20
                + adx_score * 0.25
            )

            volume_24h = float(ticker.get("quoteVolume", 0))

            return {
                "symbol": symbol,
                "score": round(composite, 2),
                "volume_24h": round(volume_24h, 2),
                "price": round(current_price, 8),
                "atr": round(atr, 8),
                "atr_pct": round(atr_pct, 4),
                "adx": round(adx, 2),
                "oi_change": round(oi_change, 4),
                "vol_spike_ratio": round(vol_spike_ratio, 2),
                "regime": regime,
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:
            logger.error("Failed to compute metrics for %s: %s", symbol, exc)
            return None

    # ── Technical indicator calculations ─────────────────────────

    @staticmethod
    def _calculate_atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int,
    ) -> float:
        """Calculate Average True Range (ATR).

        Args:
            highs: Array of high prices.
            lows: Array of low prices.
            closes: Array of close prices.
            period: ATR lookback period.

        Returns:
            Current ATR value.
        """
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        true_range = np.maximum(tr1, np.maximum(tr2, tr3))

        if len(true_range) < period:
            return float(np.mean(true_range)) if len(true_range) > 0 else 0.0

        # Wilder's smoothing (EMA with alpha = 1/period)
        atr_values = np.zeros(len(true_range))
        atr_values[period - 1] = np.mean(true_range[:period])
        for i in range(period, len(true_range)):
            atr_values[i] = (
                atr_values[i - 1] * (period - 1) + true_range[i]
            ) / period

        return float(atr_values[-1])

    @staticmethod
    def _calculate_adx(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int,
    ) -> float:
        """Calculate Average Directional Index (ADX).

        Args:
            highs: Array of high prices.
            lows: Array of low prices.
            closes: Array of close prices.
            period: ADX lookback period.

        Returns:
            Current ADX value.
        """
        n = len(closes)
        if n < period * 2:
            return 0.0

        # Directional movement
        up_move = highs[1:] - highs[:-1]
        down_move = lows[:-1] - lows[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True range
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        true_range = np.maximum(tr1, np.maximum(tr2, tr3))

        # Wilder's smoothing
        def smooth(arr: np.ndarray, p: int) -> np.ndarray:
            result = np.zeros(len(arr))
            result[p - 1] = np.sum(arr[:p])
            for i in range(p, len(arr)):
                result[i] = result[i - 1] - (result[i - 1] / p) + arr[i]
            return result

        atr_smooth = smooth(true_range, period)
        plus_dm_smooth = smooth(plus_dm, period)
        minus_dm_smooth = smooth(minus_dm, period)

        # Avoid division by zero
        atr_safe = np.where(atr_smooth > 0, atr_smooth, 1.0)

        plus_di = 100 * plus_dm_smooth / atr_safe
        minus_di = 100 * minus_dm_smooth / atr_safe

        di_sum = plus_di + minus_di
        di_sum_safe = np.where(di_sum > 0, di_sum, 1.0)
        dx = 100 * np.abs(plus_di - minus_di) / di_sum_safe

        # Smooth DX to get ADX
        adx_values = smooth(dx, period)
        # Normalise the final ADX by period
        final_adx = adx_values[-1] / period if adx_values[-1] > 0 else 0.0

        return float(min(final_adx, 100.0))

    @staticmethod
    def _classify_regime(
        adx: float, atr_pct: float, vol_spike: float
    ) -> str:
        """Classify the current market regime.

        Args:
            adx: Average Directional Index value.
            atr_pct: ATR as percentage of price.
            vol_spike: Volume spike ratio.

        Returns:
            Regime label string.
        """
        if adx > 40 and atr_pct > 2.0:
            return "strong_trend"
        elif adx > 25:
            return "trending"
        elif atr_pct > 3.0 and vol_spike > 2.0:
            return "volatile_breakout"
        elif atr_pct < 0.5:
            return "low_volatility"
        elif vol_spike > 3.0:
            return "volume_spike"
        else:
            return "ranging"

    # ── Open Interest change ─────────────────────────────────────

    async def _get_oi_change(self, symbol: str) -> float:
        """Fetch recent open interest change percentage.

        Uses the ``/fapi/v1/openInterest`` endpoint and compares against
        the cached previous value in Redis.

        Returns:
            OI change as a decimal fraction (e.g. 0.05 = 5% increase).
        """
        if not self._binance_client or not self._redis:
            return 0.0

        try:
            oi_data = await self._binance_client.futures_open_interest(
                symbol=symbol
            )
            current_oi = float(oi_data.get("openInterest", 0))

            redis_key = f"{REDIS_KEY_SCANNER}:oi:{symbol}"
            prev_oi_raw = await self._redis.get(redis_key)
            prev_oi = float(prev_oi_raw) if prev_oi_raw else current_oi

            # Store current OI for next comparison (60s TTL)
            await self._redis.set(redis_key, str(current_oi), ex=60)

            if prev_oi > 0:
                return (current_oi - prev_oi) / prev_oi
            return 0.0

        except Exception as exc:
            logger.debug("OI fetch failed for %s: %s", symbol, exc)
            return 0.0

    # ── Caching ──────────────────────────────────────────────────

    async def _cache_results(self, pairs: list[dict[str, Any]]) -> None:
        """Store ranked pairs in Redis with TTL.

        Args:
            pairs: List of scored pair dicts.
        """
        if not self._redis:
            return

        try:
            cache_data = json.dumps(pairs)
            await self._redis.set(
                f"{REDIS_KEY_SCANNER}:ranked_pairs",
                cache_data,
                ex=SCANNER_CACHE_TTL,
            )
        except Exception as exc:
            logger.error("Failed to cache scanner results: %s", exc)

    async def get_ranked_pairs(self) -> list[dict[str, Any]]:
        """Return cached scan results from Redis.

        Returns:
            List of ranked pair dicts, or empty list if cache miss.
        """
        if not self._redis:
            self._redis = await get_redis()

        try:
            raw = await self._redis.get(f"{REDIS_KEY_SCANNER}:ranked_pairs")
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.error("Failed to read cached pairs: %s", exc)

        return []

    # ── Database persistence ─────────────────────────────────────

    async def _persist_snapshots(self, pairs: list[dict[str, Any]]) -> None:
        """Write MarketSnapshot records to the database.

        Args:
            pairs: List of scored pair dicts to persist.
        """
        if not pairs:
            return

        try:
            async with async_session_factory() as session:
                async with session.begin():
                    now = datetime.now(timezone.utc)
                    for pair in pairs:
                        snapshot = MarketSnapshot(
                            symbol=pair["symbol"],
                            price=pair["price"],
                            volume_24h=pair["volume_24h"],
                            open_interest=pair.get("oi_change"),
                            atr=pair.get("atr"),
                            adx=pair.get("adx"),
                            regime=pair.get("regime"),
                            scanner_score=pair["score"],
                            captured_at=now,
                        )
                        session.add(snapshot)

            logger.debug("Persisted %d market snapshots", len(pairs))
        except Exception as exc:
            logger.error("Failed to persist market snapshots: %s", exc)

    # ── Utility ──────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Return whether the scanner loop is active."""
        return self._running

    @property
    def last_scan_duration(self) -> float:
        """Return the duration of the last scan in seconds."""
        return self._last_scan_time
