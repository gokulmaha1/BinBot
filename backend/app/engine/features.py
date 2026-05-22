"""
BinBot AI Auto Mode — Feature Extraction Engine
Computes all technical indicators from OHLCV candle data.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

CACHE_TTL_SECONDS: int = 15
SWING_LOOKBACK: int = 5
VOLUME_AVG_PERIOD: int = 20
SR_CLUSTER_ATR_MULT: float = 0.5  # merge S/R within 0.5 ATR


@dataclass
class FeatureSet:
    """Container for all computed technical features."""

    symbol: str
    timestamp: str  # ISO-formatted time of the latest candle

    # ── Trend ────────────────────────────────────────────────────
    ema_fast: float = 0.0        # EMA 9
    ema_mid: float = 0.0         # EMA 21
    ema_slow: float = 0.0        # EMA 50
    ema_trend: float = 0.0       # EMA 200
    supertrend: float = 0.0
    supertrend_direction: int = 1  # 1 = bullish, -1 = bearish
    vwap: float = 0.0

    # ── Momentum ─────────────────────────────────────────────────
    rsi: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    stoch_rsi_k: float = 50.0
    stoch_rsi_d: float = 50.0

    # ── Volatility ───────────────────────────────────────────────
    atr: float = 0.0
    atr_avg_20: float = 0.0      # 20-period average of ATR
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0        # normalised BB width
    bb_percent_b: float = 0.5    # %B: (price - lower) / (upper - lower)

    # ── Volume ───────────────────────────────────────────────────
    obv: float = 0.0
    obv_slope: float = 0.0       # OBV change over last 5 bars
    volume_spike_ratio: float = 1.0  # current volume / 20-period avg

    # ── Market Structure ─────────────────────────────────────────
    swing_highs: list[float] = field(default_factory=list)
    swing_lows: list[float] = field(default_factory=list)
    nearest_resistance: float = 0.0
    nearest_support: float = 0.0
    support_zones: list[float] = field(default_factory=list)
    resistance_zones: list[float] = field(default_factory=list)

    # ── ADX (derived from ATR internals, needed by regime) ───────
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0

    # ── Raw price info ───────────────────────────────────────────
    close: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    volume: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to JSON-safe dictionary."""
        return asdict(self)


class FeatureExtractor:
    """Extracts all technical features from OHLCV DataFrame."""

    def __init__(self, redis: Optional[aioredis.Redis] = None) -> None:
        self._redis = redis

    # ── Public API ───────────────────────────────────────────────

    async def extract(self, df: pd.DataFrame, symbol: str) -> FeatureSet:
        """
        Compute every indicator and return a FeatureSet.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain columns: open, high, low, close, volume.
            Rows sorted ascending by time (oldest first).
        symbol : str
            Trading pair symbol, e.g. "BTCUSDT".

        Returns
        -------
        FeatureSet
        """
        cache_key = f"features:{symbol}"

        # Try cache first
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
                if cached is not None:
                    data = json.loads(cached)
                    logger.debug("Feature cache hit for %s", symbol)
                    return FeatureSet(**data)
            except Exception:
                logger.warning("Redis cache read failed for %s", symbol, exc_info=True)

        # Validate input
        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            raise ValueError(
                f"DataFrame missing columns: {required_cols - set(df.columns)}"
            )
        if len(df) < settings.EMA_TREND:
            logger.warning(
                "DataFrame has %d rows, need at least %d for EMA %d — "
                "results may be inaccurate",
                len(df), settings.EMA_TREND, settings.EMA_TREND,
            )

        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        open_ = df["open"].astype(float).values
        volume = df["volume"].astype(float).values

        # Compute indicators
        ema_fast = self._ema(close, settings.EMA_FAST)
        ema_mid = self._ema(close, settings.EMA_MID)
        ema_slow = self._ema(close, settings.EMA_SLOW)
        ema_trend = self._ema(close, settings.EMA_TREND)

        rsi = self._rsi(close, settings.RSI_PERIOD)
        macd_line, macd_signal_line, macd_hist = self._macd(
            close, settings.MACD_FAST, settings.MACD_SLOW, settings.MACD_SIGNAL
        )
        stoch_k, stoch_d = self._stochastic_rsi(
            close, settings.RSI_PERIOD, settings.RSI_PERIOD, 3, 3
        )

        atr_series = self._atr(high, low, close, settings.ATR_PERIOD)
        atr_val = float(atr_series[-1])
        atr_avg = float(np.nanmean(atr_series[-VOLUME_AVG_PERIOD:]))

        bb_upper, bb_mid, bb_lower = self._bollinger_bands(
            close, settings.BB_PERIOD, settings.BB_STD
        )

        st_val, st_dir = self._supertrend(
            high, low, close,
            settings.SUPERTREND_PERIOD, settings.SUPERTREND_MULTIPLIER,
        )

        vwap = self._vwap(high, low, close, volume)

        obv_series = self._obv(close, volume)
        vol_spike = self._volume_spike_ratio(volume, VOLUME_AVG_PERIOD)

        adx_val, plus_di, minus_di = self._adx(high, low, close, settings.ADX_PERIOD)

        swing_h, swing_l = self._swing_points(high, low, SWING_LOOKBACK)
        sup_zones, res_zones = self._support_resistance_zones(
            swing_h, swing_l, atr_val
        )

        current_close = float(close[-1])
        bb_u = float(bb_upper[-1])
        bb_l = float(bb_lower[-1])
        bb_w = (bb_u - bb_l) / float(bb_mid[-1]) if bb_mid[-1] != 0 else 0.0
        pct_b = (current_close - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) != 0 else 0.5

        nearest_sup = max(
            (s for s in sup_zones if s < current_close), default=0.0
        )
        nearest_res = min(
            (r for r in res_zones if r > current_close), default=0.0
        )

        ts = str(df.index[-1]) if isinstance(df.index, pd.DatetimeIndex) else str(len(df) - 1)

        fs = FeatureSet(
            symbol=symbol,
            timestamp=ts,
            # Trend
            ema_fast=float(ema_fast[-1]),
            ema_mid=float(ema_mid[-1]),
            ema_slow=float(ema_slow[-1]),
            ema_trend=float(ema_trend[-1]),
            supertrend=float(st_val),
            supertrend_direction=int(st_dir),
            vwap=float(vwap),
            # Momentum
            rsi=float(rsi[-1]),
            macd_line=float(macd_line[-1]),
            macd_signal=float(macd_signal_line[-1]),
            macd_histogram=float(macd_hist[-1]),
            stoch_rsi_k=float(stoch_k[-1]),
            stoch_rsi_d=float(stoch_d[-1]),
            # Volatility
            atr=atr_val,
            atr_avg_20=atr_avg,
            bb_upper=bb_u,
            bb_middle=float(bb_mid[-1]),
            bb_lower=bb_l,
            bb_width=bb_w,
            bb_percent_b=pct_b,
            # Volume
            obv=float(obv_series[-1]),
            obv_slope=float(obv_series[-1] - obv_series[-6]) if len(obv_series) >= 6 else 0.0,
            volume_spike_ratio=float(vol_spike),
            # Market Structure
            swing_highs=[float(h) for h in swing_h[-10:]],
            swing_lows=[float(l) for l in swing_l[-10:]],
            nearest_resistance=nearest_res,
            nearest_support=nearest_sup,
            support_zones=sup_zones,
            resistance_zones=res_zones,
            # ADX
            adx=float(adx_val),
            plus_di=float(plus_di),
            minus_di=float(minus_di),
            # Raw
            close=current_close,
            high=float(high[-1]),
            low=float(low[-1]),
            open=float(open_[-1]),
            volume=float(volume[-1]),
        )

        # Cache result
        if self._redis is not None:
            try:
                await self._redis.setex(
                    cache_key, CACHE_TTL_SECONDS, json.dumps(fs.to_dict())
                )
            except Exception:
                logger.warning("Redis cache write failed for %s", symbol, exc_info=True)

        logger.info("Features extracted for %s — close=%.4f rsi=%.2f atr=%.4f", symbol, fs.close, fs.rsi, fs.atr)
        return fs

    # ── Private indicator methods ────────────────────────────────

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average."""
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(data, dtype=float)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _sma(data: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average (NaN-padded)."""
        out = np.full_like(data, np.nan, dtype=float)
        if len(data) < period:
            return out
        cumsum = np.cumsum(data, dtype=float)
        cumsum[period:] = cumsum[period:] - cumsum[:-period]
        out[period - 1:] = cumsum[period - 1:] / period
        return out

    @staticmethod
    def _rsi(close: np.ndarray, period: int) -> np.ndarray:
        """Relative Strength Index using Wilder smoothing."""
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.empty(len(close), dtype=float)
        avg_loss = np.empty(len(close), dtype=float)
        rsi = np.full(len(close), 50.0, dtype=float)

        avg_gain[0] = 0.0
        avg_loss[0] = 0.0

        if len(close) <= period:
            return rsi

        # First average
        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])

        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

        for i in range(period, len(close)):
            if avg_loss[i] == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    def _macd(
        self,
        close: np.ndarray,
        fast: int,
        slow: int,
        signal: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """MACD Line, Signal Line, Histogram."""
        ema_fast = self._ema(close, fast)
        ema_slow = self._ema(close, slow)
        macd_line = ema_fast - ema_slow
        macd_signal = self._ema(macd_line, signal)
        histogram = macd_line - macd_signal
        return macd_line, macd_signal, histogram

    def _stochastic_rsi(
        self,
        close: np.ndarray,
        rsi_period: int,
        stoch_period: int,
        k_smooth: int,
        d_smooth: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Stochastic RSI (%K, %D)."""
        rsi = self._rsi(close, rsi_period)
        n = len(rsi)
        stoch_rsi = np.full(n, 50.0, dtype=float)

        for i in range(stoch_period - 1, n):
            window = rsi[i - stoch_period + 1 : i + 1]
            min_rsi = np.nanmin(window)
            max_rsi = np.nanmax(window)
            if max_rsi - min_rsi == 0:
                stoch_rsi[i] = 50.0
            else:
                stoch_rsi[i] = ((rsi[i] - min_rsi) / (max_rsi - min_rsi)) * 100.0

        k = self._sma(stoch_rsi, k_smooth)
        d = self._sma(k, d_smooth)

        # Fill NaN with 50
        k = np.where(np.isnan(k), 50.0, k)
        d = np.where(np.isnan(d), 50.0, d)
        return k, d

    @staticmethod
    def _true_range(
        high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> np.ndarray:
        """True Range array."""
        n = len(high)
        tr = np.empty(n, dtype=float)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        return tr

    def _atr(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int,
    ) -> np.ndarray:
        """Average True Range using Wilder smoothing."""
        tr = self._true_range(high, low, close)
        atr = np.empty_like(tr, dtype=float)
        atr[:period] = np.nan

        if len(tr) < period:
            return np.full_like(tr, np.nan, dtype=float)

        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def _bollinger_bands(
        self, close: np.ndarray, period: int, num_std: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bollinger Bands (upper, middle, lower)."""
        mid = self._sma(close, period)
        n = len(close)
        std = np.full(n, 0.0, dtype=float)
        for i in range(period - 1, n):
            std[i] = np.std(close[i - period + 1 : i + 1], ddof=0)
        upper = mid + num_std * std
        lower = mid - num_std * std
        # Fill NaN edges with close price
        upper = np.where(np.isnan(upper), close, upper)
        mid = np.where(np.isnan(mid), close, mid)
        lower = np.where(np.isnan(lower), close, lower)
        return upper, mid, lower

    def _supertrend(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int,
        multiplier: float,
    ) -> tuple[float, int]:
        """Supertrend value and direction at last bar."""
        atr = self._atr(high, low, close, period)
        n = len(close)

        upper_band = np.empty(n, dtype=float)
        lower_band = np.empty(n, dtype=float)
        supertrend = np.empty(n, dtype=float)
        direction = np.ones(n, dtype=int)  # 1 = up (bullish)

        hl2 = (high + low) / 2.0

        for i in range(n):
            atr_val = atr[i] if not np.isnan(atr[i]) else 0.0
            upper_band[i] = hl2[i] + multiplier * atr_val
            lower_band[i] = hl2[i] - multiplier * atr_val

        for i in range(1, n):
            # Adjust bands
            if lower_band[i] > lower_band[i - 1] or close[i - 1] < lower_band[i - 1]:
                pass  # keep current lower_band
            else:
                lower_band[i] = lower_band[i - 1]

            if upper_band[i] < upper_band[i - 1] or close[i - 1] > upper_band[i - 1]:
                pass  # keep current upper_band
            else:
                upper_band[i] = upper_band[i - 1]

            # Direction
            if supertrend[i - 1] == upper_band[i - 1]:
                direction[i] = -1 if close[i] > upper_band[i] else -1
                if close[i] > upper_band[i]:
                    direction[i] = 1
                else:
                    direction[i] = -1
            else:
                if close[i] < lower_band[i]:
                    direction[i] = -1
                else:
                    direction[i] = 1

            supertrend[i] = lower_band[i] if direction[i] == 1 else upper_band[i]

        supertrend[0] = lower_band[0]
        return float(supertrend[-1]), int(direction[-1])

    @staticmethod
    def _vwap(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> float:
        """Volume Weighted Average Price (intraday session)."""
        typical_price = (high + low + close) / 3.0
        cum_tp_vol = np.cumsum(typical_price * volume)
        cum_vol = np.cumsum(volume)
        if cum_vol[-1] == 0:
            return float(close[-1])
        return float(cum_tp_vol[-1] / cum_vol[-1])

    @staticmethod
    def _obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """On-Balance Volume."""
        obv = np.empty(len(close), dtype=float)
        obv[0] = volume[0]
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + volume[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - volume[i]
            else:
                obv[i] = obv[i - 1]
        return obv

    @staticmethod
    def _volume_spike_ratio(volume: np.ndarray, period: int) -> float:
        """Ratio of current volume to *period*-bar average."""
        if len(volume) < period + 1:
            return 1.0
        avg = np.mean(volume[-period - 1 : -1])
        if avg == 0:
            return 1.0
        return float(volume[-1] / avg)

    def _adx(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int,
    ) -> tuple[float, float, float]:
        """Average Directional Index, +DI, -DI."""
        n = len(close)
        if n < period + 1:
            return 0.0, 0.0, 0.0

        plus_dm = np.zeros(n, dtype=float)
        minus_dm = np.zeros(n, dtype=float)

        for i in range(1, n):
            up_move = high[i] - high[i - 1]
            down_move = low[i - 1] - low[i]
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move

        tr = self._true_range(high, low, close)

        # Wilder smoothing
        atr_smooth = np.zeros(n, dtype=float)
        plus_dm_smooth = np.zeros(n, dtype=float)
        minus_dm_smooth = np.zeros(n, dtype=float)

        atr_smooth[period] = np.sum(tr[1 : period + 1])
        plus_dm_smooth[period] = np.sum(plus_dm[1 : period + 1])
        minus_dm_smooth[period] = np.sum(minus_dm[1 : period + 1])

        for i in range(period + 1, n):
            atr_smooth[i] = atr_smooth[i - 1] - (atr_smooth[i - 1] / period) + tr[i]
            plus_dm_smooth[i] = plus_dm_smooth[i - 1] - (plus_dm_smooth[i - 1] / period) + plus_dm[i]
            minus_dm_smooth[i] = minus_dm_smooth[i - 1] - (minus_dm_smooth[i - 1] / period) + minus_dm[i]

        # DI
        plus_di = np.zeros(n, dtype=float)
        minus_di = np.zeros(n, dtype=float)
        dx = np.zeros(n, dtype=float)

        for i in range(period, n):
            if atr_smooth[i] != 0:
                plus_di[i] = (plus_dm_smooth[i] / atr_smooth[i]) * 100.0
                minus_di[i] = (minus_dm_smooth[i] / atr_smooth[i]) * 100.0
            di_sum = plus_di[i] + minus_di[i]
            if di_sum != 0:
                dx[i] = (abs(plus_di[i] - minus_di[i]) / di_sum) * 100.0

        # ADX = smoothed DX
        adx_arr = np.zeros(n, dtype=float)
        start = 2 * period
        if start < n:
            adx_arr[start] = np.mean(dx[period : start + 1])
            for i in range(start + 1, n):
                adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

        return float(adx_arr[-1]), float(plus_di[-1]), float(minus_di[-1])

    @staticmethod
    def _swing_points(
        high: np.ndarray, low: np.ndarray, lookback: int
    ) -> tuple[list[float], list[float]]:
        """Detect swing highs and swing lows with *lookback* bars either side."""
        swing_highs: list[float] = []
        swing_lows: list[float] = []
        n = len(high)

        for i in range(lookback, n - lookback):
            if high[i] == np.max(high[i - lookback : i + lookback + 1]):
                swing_highs.append(float(high[i]))
            if low[i] == np.min(low[i - lookback : i + lookback + 1]):
                swing_lows.append(float(low[i]))

        return swing_highs, swing_lows

    @staticmethod
    def _support_resistance_zones(
        swing_highs: list[float],
        swing_lows: list[float],
        atr: float,
    ) -> tuple[list[float], list[float]]:
        """Cluster swing points into S/R zones (merge within 0.5 ATR)."""
        threshold = SR_CLUSTER_ATR_MULT * atr if atr > 0 else 0.0

        def cluster(points: list[float]) -> list[float]:
            if not points:
                return []
            sorted_pts = sorted(points)
            clusters: list[list[float]] = [[sorted_pts[0]]]
            for p in sorted_pts[1:]:
                if threshold > 0 and (p - clusters[-1][-1]) <= threshold:
                    clusters[-1].append(p)
                else:
                    clusters.append([p])
            return [round(float(np.mean(c)), 8) for c in clusters]

        return cluster(swing_lows), cluster(swing_highs)
