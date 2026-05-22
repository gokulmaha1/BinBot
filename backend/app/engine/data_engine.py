"""
BinBot AI Auto Mode — Market Data Engine
Real-time market data ingestion via Binance Futures WebSocket.

Responsibilities:
- Subscribe to kline, miniTicker, depth, markPrice, and liquidation streams
- Maintain rolling OHLCV candle buffers in Redis (sorted sets)
- Auto-reconnect with exponential backoff
- Heartbeat monitoring (ping/pong)
- Missing candle recovery via REST on reconnect
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)
from binance import AsyncClient

import redis.asyncio as aioredis

from app.config import settings
from app.deps import get_redis

logger = logging.getLogger("binbot.data_engine")

# ── Constants ────────────────────────────────────────────────────
BINANCE_WS_BASE = "wss://fstream.binance.com"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com"
REDIS_KEY_PREFIX = "binbot:market"
RECONNECT_BASE_DELAY = 1.0  # seconds
MAX_COMBINED_STREAMS = 200  # Binance limit per connection


# ── Candle Buffer ────────────────────────────────────────────────

class CandleBuffer:
    """Manages rolling OHLCV candle windows stored in Redis sorted sets.

    Each candle is stored as a JSON member in a sorted set, scored by
    its open timestamp. The set is trimmed to keep the latest N candles
    (configured via ``settings.CANDLE_BUFFER_SIZE``).
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._max_size = settings.CANDLE_BUFFER_SIZE

    def _key(self, symbol: str, timeframe: str) -> str:
        """Build Redis sorted-set key for a symbol/timeframe pair."""
        return f"{REDIS_KEY_PREFIX}:candles:{symbol}:{timeframe}"

    async def add_candle(
        self,
        symbol: str,
        timeframe: str,
        candle: dict[str, Any],
    ) -> None:
        """Insert or update a candle and trim the buffer.

        Args:
            symbol: Trading pair, e.g. ``BTCUSDT``.
            timeframe: Kline interval, e.g. ``1m``.
            candle: Dict with keys ``t, o, h, l, c, v`` (open time,
                    open, high, low, close, volume).
        """
        key = self._key(symbol, timeframe)
        open_time = candle["t"]

        # Remove any existing candle with the same open time (update)
        existing = await self._redis.zrangebyscore(key, open_time, open_time)
        if existing:
            await self._redis.zrem(key, *existing)

        await self._redis.zadd(key, {json.dumps(candle): float(open_time)})

        # Trim: keep only the latest ``_max_size`` candles
        count = await self._redis.zcard(key)
        if count > self._max_size:
            await self._redis.zremrangebyrank(key, 0, count - self._max_size - 1)

    async def add_candles_bulk(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]],
    ) -> None:
        """Bulk-insert candles (used during REST recovery).

        Args:
            symbol: Trading pair.
            timeframe: Kline interval.
            candles: List of candle dicts.
        """
        if not candles:
            return

        key = self._key(symbol, timeframe)
        mapping: dict[str, float] = {}
        for c in candles:
            mapping[json.dumps(c)] = float(c["t"])

        await self._redis.zadd(key, mapping)

        # Trim
        count = await self._redis.zcard(key)
        if count > self._max_size:
            await self._redis.zremrangebyrank(key, 0, count - self._max_size - 1)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve the most recent candles from the buffer.

        Args:
            symbol: Trading pair.
            timeframe: Kline interval.
            limit: Maximum number of candles to return.

        Returns:
            List of candle dicts ordered oldest-first.
        """
        key = self._key(symbol, timeframe)
        raw = await self._redis.zrange(key, -limit, -1)
        return [json.loads(r) for r in raw]

    async def clear(self, symbol: str, timeframe: str) -> None:
        """Delete the entire buffer for a symbol/timeframe."""
        await self._redis.delete(self._key(symbol, timeframe))


# ── Market Data Engine ───────────────────────────────────────────

class MarketDataEngine:
    """Real-time market data engine using Binance Futures WebSocket.

    Features:
      - Multi-stream combined WebSocket connections
      - Automatic reconnect with exponential backoff
      - Heartbeat ping/pong monitoring
      - Rolling candle buffers in Redis
      - Missing candle recovery via REST API
      - Live ticker, orderbook, and mark-price caching
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._candle_buffer: Optional[CandleBuffer] = None
        self._binance_client: Optional[AsyncClient] = None

        # Subscribed symbols
        self._symbols: set[str] = set()

        # WebSocket state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._reconnect_delay: float = RECONNECT_BASE_DELAY
        self._last_pong: float = 0.0

        # In-memory caches (also backed by Redis)
        self._tickers: dict[str, dict[str, Any]] = {}
        self._orderbooks: dict[str, dict[str, Any]] = {}
        self._mark_prices: dict[str, float] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, symbols: list[str]) -> None:
        """Initialise resources and begin streaming for the given symbols.

        Args:
            symbols: List of trading pairs, e.g. ``["BTCUSDT", "ETHUSDT"]``.
        """
        if self._running:
            logger.warning("MarketDataEngine already running — ignoring start()")
            return

        logger.info("Starting MarketDataEngine for %d symbols", len(symbols))

        self._redis = await get_redis()
        self._candle_buffer = CandleBuffer(self._redis)

        # Initialise python-binance async client (for REST recovery)
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

        self._symbols = {s.upper() for s in symbols}
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_connect_loop())
        logger.info("MarketDataEngine started")

    async def stop(self) -> None:
        """Gracefully shut down all connections and tasks."""
        logger.info("Stopping MarketDataEngine")
        self._running = False

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._binance_client:
            await self._binance_client.close_connection()
            self._binance_client = None

        logger.info("MarketDataEngine stopped")

    # ── Dynamic subscription ─────────────────────────────────────

    async def subscribe(self, symbol: str) -> None:
        """Add a symbol to the active subscription set and reconnect.

        Args:
            symbol: Trading pair to subscribe, e.g. ``BTCUSDT``.
        """
        symbol = symbol.upper()
        if symbol in self._symbols:
            logger.debug("Already subscribed to %s", symbol)
            return

        logger.info("Subscribing to %s", symbol)
        self._symbols.add(symbol)

        # Request stream addition via WebSocket SUBSCRIBE method
        if self._ws:
            streams = self._build_streams_for_symbol(symbol)
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": streams,
                "id": int(time.time() * 1000),
            }
            try:
                await self._ws.send(json.dumps(subscribe_msg))
            except Exception as exc:
                logger.error("Failed to subscribe to %s: %s", symbol, exc)

        # Recover historical candles for the new symbol
        await self._recover_candles(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        """Remove a symbol from the active subscription set.

        Args:
            symbol: Trading pair to unsubscribe.
        """
        symbol = symbol.upper()
        if symbol not in self._symbols:
            return

        logger.info("Unsubscribing from %s", symbol)
        self._symbols.discard(symbol)

        if self._ws:
            streams = self._build_streams_for_symbol(symbol)
            unsub_msg = {
                "method": "UNSUBSCRIBE",
                "params": streams,
                "id": int(time.time() * 1000),
            }
            try:
                await self._ws.send(json.dumps(unsub_msg))
            except Exception as exc:
                logger.error("Failed to unsubscribe from %s: %s", symbol, exc)

    # ── Data accessors ───────────────────────────────────────────

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve candles from the rolling Redis buffer.

        Args:
            symbol: Trading pair.
            timeframe: Kline interval (``1m``, ``5m``, ``15m``, ``1h``, ``4h``).
            limit: Max candles to return.

        Returns:
            List of candle dicts (oldest first).
        """
        if not self._candle_buffer:
            return []
        return await self._candle_buffer.get_candles(symbol.upper(), timeframe, limit)

    async def get_ticker(self, symbol: str) -> Optional[dict[str, Any]]:
        """Return the latest cached mini-ticker for a symbol.

        Falls back to Redis if not in memory.
        """
        symbol = symbol.upper()
        if symbol in self._tickers:
            return self._tickers[symbol]

        if self._redis:
            raw = await self._redis.get(f"{REDIS_KEY_PREFIX}:ticker:{symbol}")
            if raw:
                return json.loads(raw)
        return None

    async def get_orderbook(self, symbol: str) -> Optional[dict[str, Any]]:
        """Return the latest cached depth-5 orderbook snapshot.

        Falls back to Redis if not in memory.
        """
        symbol = symbol.upper()
        if symbol in self._orderbooks:
            return self._orderbooks[symbol]

        if self._redis:
            raw = await self._redis.get(f"{REDIS_KEY_PREFIX}:depth:{symbol}")
            if raw:
                return json.loads(raw)
        return None

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Return the latest cached mark price for a symbol."""
        symbol = symbol.upper()
        if symbol in self._mark_prices:
            return self._mark_prices[symbol]

        if self._redis:
            raw = await self._redis.get(f"{REDIS_KEY_PREFIX}:mark:{symbol}")
            if raw:
                return float(raw)
        return None

    # ── Stream building ──────────────────────────────────────────

    def _build_streams_for_symbol(self, symbol: str) -> list[str]:
        """Build the list of stream names for a single symbol."""
        sym_lower = symbol.lower()
        streams: list[str] = []

        # Kline streams for each configured timeframe
        for tf in settings.TIMEFRAMES:
            streams.append(f"{sym_lower}@kline_{tf}")

        # Mini ticker
        streams.append(f"{sym_lower}@miniTicker")

        # Partial book depth (top 5 levels, 100ms updates)
        streams.append(f"{sym_lower}@depth5@100ms")

        # Mark price (1s updates)
        streams.append(f"{sym_lower}@markPrice@1s")

        return streams

    def _build_all_streams(self) -> list[str]:
        """Build the combined stream list for all subscribed symbols."""
        streams: list[str] = []
        for symbol in self._symbols:
            streams.extend(self._build_streams_for_symbol(symbol))

        # Global liquidation stream
        streams.append("!forceOrder@arr")

        return streams

    def _build_ws_url(self) -> str:
        """Construct the combined-stream WebSocket URL."""
        base = BINANCE_WS_TESTNET if settings.is_testnet else BINANCE_WS_BASE
        streams = self._build_all_streams()

        # Binance limits 200 streams per connection
        if len(streams) > MAX_COMBINED_STREAMS:
            logger.warning(
                "Stream count (%d) exceeds Binance limit (%d). Truncating.",
                len(streams),
                MAX_COMBINED_STREAMS,
            )
            streams = streams[:MAX_COMBINED_STREAMS]

        stream_path = "/".join(streams)
        return f"{base}/stream?streams={stream_path}"

    # ── WebSocket connection loop ────────────────────────────────

    async def _ws_connect_loop(self) -> None:
        """Main connection loop with exponential backoff reconnect."""
        while self._running:
            try:
                url = self._build_ws_url()
                logger.info(
                    "Connecting to Binance WebSocket (%d streams)",
                    len(self._build_all_streams()),
                )

                async with websockets.connect(
                    url,
                    ping_interval=None,  # We manage pings manually
                    ping_timeout=None,
                    close_timeout=5,
                    max_size=10 * 1024 * 1024,  # 10 MB
                ) as ws:
                    self._ws = ws
                    self._last_pong = time.monotonic()
                    self._reconnect_delay = RECONNECT_BASE_DELAY  # reset backoff

                    logger.info("WebSocket connected")

                    # Recover missing candles on reconnect
                    await self._recover_all_candles()

                    # Start heartbeat monitor
                    self._heartbeat_task = asyncio.create_task(
                        self._heartbeat_loop(ws)
                    )

                    # Message processing loop
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            await self._handle_message(raw_msg)
                        except Exception as exc:
                            logger.error(
                                "Error processing message: %s", exc, exc_info=True
                            )

            except (
                ConnectionClosed,
                ConnectionClosedError,
                ConnectionClosedOK,
            ) as exc:
                logger.warning("WebSocket closed: %s", exc)
            except asyncio.CancelledError:
                logger.info("WebSocket task cancelled")
                return
            except Exception as exc:
                logger.error("WebSocket error: %s", exc, exc_info=True)

            # Cancel heartbeat
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()

            self._ws = None

            if not self._running:
                return

            # Exponential backoff
            logger.info(
                "Reconnecting in %.1fs (backoff)", self._reconnect_delay
            )
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2,
                float(settings.WS_RECONNECT_MAX_DELAY),
            )

    # ── Heartbeat ────────────────────────────────────────────────

    async def _heartbeat_loop(
        self, ws: websockets.WebSocketClientProtocol
    ) -> None:
        """Send periodic pings and close the socket if pong times out."""
        while self._running:
            try:
                await asyncio.sleep(settings.WS_PING_INTERVAL)

                # Send ping
                pong_waiter = await ws.ping()
                try:
                    await asyncio.wait_for(
                        pong_waiter, timeout=settings.WS_PING_TIMEOUT
                    )
                    self._last_pong = time.monotonic()
                except asyncio.TimeoutError:
                    logger.warning(
                        "Pong timeout (%ds) — forcing reconnect",
                        settings.WS_PING_TIMEOUT,
                    )
                    await ws.close(code=4000, reason="Pong timeout")
                    return

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("Heartbeat error: %s", exc)
                return

    # ── Message handling ─────────────────────────────────────────

    async def _handle_message(self, raw: str) -> None:
        """Route an incoming WebSocket message to the appropriate handler."""
        data = json.loads(raw)

        # Combined-stream wrapper: {"stream": "...", "data": {...}}
        stream = data.get("stream", "")
        payload = data.get("data", data)

        if "@kline_" in stream:
            await self._handle_kline(payload)
        elif "@miniTicker" in stream:
            await self._handle_ticker(payload)
        elif "@depth" in stream:
            await self._handle_depth(stream, payload)
        elif "@markPrice" in stream:
            await self._handle_mark_price(payload)
        elif "forceOrder" in stream:
            await self._handle_liquidation(payload)
        elif "result" in data:
            # Subscribe/unsubscribe acknowledgement
            logger.debug("WS ack: %s", data)
        else:
            logger.debug("Unhandled stream: %s", stream)

    async def _handle_kline(self, data: dict[str, Any]) -> None:
        """Process a kline (candlestick) message."""
        k = data.get("k", {})
        symbol = k.get("s", "")
        interval = k.get("i", "")
        is_closed = k.get("x", False)

        candle = {
            "t": k.get("t"),       # Open time (ms)
            "o": float(k.get("o", 0)),
            "h": float(k.get("h", 0)),
            "l": float(k.get("l", 0)),
            "c": float(k.get("c", 0)),
            "v": float(k.get("v", 0)),
            "T": k.get("T"),       # Close time (ms)
            "q": float(k.get("q", 0)),  # Quote asset volume
            "n": k.get("n", 0),    # Number of trades
            "x": is_closed,
        }

        if self._candle_buffer:
            await self._candle_buffer.add_candle(symbol, interval, candle)

        # Also update latest price in-memory
        self._mark_prices[symbol] = candle["c"]

    async def _handle_ticker(self, data: dict[str, Any]) -> None:
        """Process a 24hr mini ticker message."""
        symbol = data.get("s", "")
        ticker = {
            "symbol": symbol,
            "close": float(data.get("c", 0)),
            "open": float(data.get("o", 0)),
            "high": float(data.get("h", 0)),
            "low": float(data.get("l", 0)),
            "volume": float(data.get("v", 0)),
            "quote_volume": float(data.get("q", 0)),
            "event_time": data.get("E"),
        }

        self._tickers[symbol] = ticker

        # Persist to Redis with 60s TTL
        if self._redis:
            await self._redis.set(
                f"{REDIS_KEY_PREFIX}:ticker:{symbol}",
                json.dumps(ticker),
                ex=60,
            )

    async def _handle_depth(self, stream: str, data: dict[str, Any]) -> None:
        """Process a partial book depth (top-5) snapshot."""
        # Extract symbol from stream name: "btcusdt@depth5@100ms"
        symbol = stream.split("@")[0].upper()
        orderbook = {
            "symbol": symbol,
            "bids": data.get("b", []),
            "asks": data.get("a", []),
            "event_time": data.get("E"),
            "transaction_time": data.get("T"),
        }

        self._orderbooks[symbol] = orderbook

        if self._redis:
            await self._redis.set(
                f"{REDIS_KEY_PREFIX}:depth:{symbol}",
                json.dumps(orderbook),
                ex=30,
            )

    async def _handle_mark_price(self, data: dict[str, Any]) -> None:
        """Process a mark price update."""
        symbol = data.get("s", "")
        mark = float(data.get("p", 0))
        funding = float(data.get("r", 0))

        self._mark_prices[symbol] = mark

        if self._redis:
            pipe = self._redis.pipeline()
            pipe.set(f"{REDIS_KEY_PREFIX}:mark:{symbol}", str(mark), ex=30)
            pipe.set(
                f"{REDIS_KEY_PREFIX}:funding:{symbol}",
                json.dumps({"rate": funding, "time": data.get("T")}),
                ex=60,
            )
            await pipe.execute()

    async def _handle_liquidation(self, data: dict[str, Any]) -> None:
        """Process a global liquidation event."""
        order = data.get("o", {})
        symbol = order.get("s", "")
        side = order.get("S", "")
        price = float(order.get("p", 0))
        qty = float(order.get("q", 0))

        logger.info(
            "Liquidation: %s %s %.2f @ %.4f",
            symbol,
            side,
            qty,
            price,
        )

        if self._redis:
            liq_event = {
                "symbol": symbol,
                "side": side,
                "price": price,
                "quantity": qty,
                "timestamp": data.get("E"),
            }
            await self._redis.lpush(
                f"{REDIS_KEY_PREFIX}:liquidations",
                json.dumps(liq_event),
            )
            # Keep only latest 100 liquidation events
            await self._redis.ltrim(f"{REDIS_KEY_PREFIX}:liquidations", 0, 99)

    # ── Candle recovery via REST ─────────────────────────────────

    async def _recover_all_candles(self) -> None:
        """Recover missing candles for all subscribed symbols on reconnect."""
        tasks = [self._recover_candles(sym) for sym in self._symbols]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _recover_candles(self, symbol: str) -> None:
        """Fetch historical candles via REST to fill gaps in the buffer.

        Fetches up to ``CANDLE_BUFFER_SIZE`` candles for each timeframe.
        """
        if not self._binance_client or not self._candle_buffer:
            return

        logger.info("Recovering candles for %s", symbol)
        for tf in settings.TIMEFRAMES:
            try:
                # Binance returns up to 1500 candles per request
                klines = await self._binance_client.futures_klines(
                    symbol=symbol,
                    interval=tf,
                    limit=settings.CANDLE_BUFFER_SIZE,
                )
                candles = [
                    {
                        "t": int(k[0]),
                        "o": float(k[1]),
                        "h": float(k[2]),
                        "l": float(k[3]),
                        "c": float(k[4]),
                        "v": float(k[5]),
                        "T": int(k[6]),
                        "q": float(k[7]),
                        "n": int(k[8]),
                        "x": True,  # Historical candles are always closed
                    }
                    for k in klines
                ]
                await self._candle_buffer.add_candles_bulk(symbol, tf, candles)
                logger.debug(
                    "Recovered %d %s candles for %s", len(candles), tf, symbol
                )
            except Exception as exc:
                logger.error(
                    "Failed to recover %s candles for %s: %s",
                    tf,
                    symbol,
                    exc,
                )

    # ── Utility ──────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Return whether the engine is currently active."""
        return self._running

    @property
    def subscribed_symbols(self) -> set[str]:
        """Return the current set of subscribed symbols."""
        return set(self._symbols)

    @property
    def is_connected(self) -> bool:
        """Return whether the WebSocket is currently connected."""
        return self._ws is not None and self._ws.open
