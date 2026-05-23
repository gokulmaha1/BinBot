"""
BinBot AI Auto Mode — Trade Execution Engine
Async wrapper around Binance Futures API for order management.

All orders use workingType='CONTRACT_PRICE'.
Retry failed orders 3 times with 1 s delay.
Duplicate prevention: checks existing orders before placing.
Slippage tracked on every market fill.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Trade,
    Position,
    TradeStatus,
    TradeState,
    SignalSide,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────
MAX_RETRIES: int = 3
RETRY_DELAY_SECONDS: float = 1.0
WORKING_TYPE: str = "CONTRACT_PRICE"


@dataclass
class OrderResult:
    """Single Binance order response."""

    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    status: str = ""
    price: float = 0.0
    avg_price: float = 0.0
    quantity: float = 0.0
    filled_qty: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class TradeResult:
    """Aggregate result of executing a full trade (entry + TP + SL)."""

    success: bool
    trade_id: Optional[UUID] = None
    entry_price: float = 0.0
    entry_order: Optional[OrderResult] = None
    tp_orders: list[OrderResult] = field(default_factory=list)
    sl_order: Optional[OrderResult] = None
    slippage: float = 0.0
    error: str = ""


class TradeExecutor:
    """
    Async trade execution engine for Binance Futures.

    Wraps the python-binance ``AsyncClient`` and records all trades
    in the database.
    """

    def __init__(self, client: Any = None, db_session: Optional[AsyncSession] = None) -> None:
        """
        Args:
            client: ``binance.AsyncClient`` instance (or testnet client).
            db_session: SQLAlchemy async session.
        """
        self.client = client
        self.db = db_session
        self._exchange_info_cache: Optional[dict] = None
        self._exchange_info_ts: float = 0.0
        self._last_order_error: str = ""

    async def _ensure_client(self) -> None:
        """Ensure the Binance client is initialized if not in paper mode."""
        if settings.is_paper:
            return
        if self.client is not None:
            return

        from binance import AsyncClient
        if settings.is_testnet:
            self.client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
                testnet=True,
            )
        else:
            self.client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
            )

    async def get_balance(self) -> float:
        """Get the wallet balance in USDT."""
        if settings.is_paper:
            return 10000.0

        await self._ensure_client()
        if not self.client:
            return 0.0

        try:
            account = await self.client.futures_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("walletBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching Binance balance: {e}")
            return 0.0

    # ─────────────────────────────────────────────────────────────
    #  FULL TRADE EXECUTION
    # ─────────────────────────────────────────────────────────────

    async def execute_trade(
        self,
        signal: Any,
        position_size: Any,
        tp_levels: Any,
        sl_price: float,
        bot_id: UUID,
        strategy_name: str = "auto",
        entry_price: float = 0.0,
    ) -> TradeResult:
        """
        Execute a complete trade:
        1. Set leverage + isolated margin
        2. Place market entry order
        3. Place TP1, TP2, TP3 as TAKE_PROFIT_MARKET
        4. Place SL as STOP_MARKET
        5. Record trade in DB
        """
        symbol: str = signal.symbol if hasattr(signal, "symbol") else signal["symbol"]
        raw_side = signal.side if hasattr(signal, "side") else signal["side"]
        side: str = raw_side.value if hasattr(raw_side, 'value') else str(raw_side)
        quantity: float = position_size.quantity if hasattr(position_size, "quantity") else position_size["quantity"]
        leverage: int = position_size.leverage if hasattr(position_size, "leverage") else position_size["leverage"]

        try:
            if settings.is_paper:
                # ── Paper Mode Execution ─────────────────────────
                if entry_price <= 0:
                    # Fallback to Redis cache for mark price
                    try:
                        from app.deps import get_redis
                        redis = await get_redis()
                        raw = await redis.get(f"binbot:market:mark:{symbol.upper()}")
                        if raw:
                            entry_price = float(raw)
                    except Exception:
                        pass
                if entry_price <= 0:
                    entry_price = 1.0  # Safe fallback

                # Create mock entry order
                entry_order = OrderResult(
                    order_id=f"paper_{uuid.uuid4().hex[:12]}",
                    client_order_id=f"paper_cl_{uuid.uuid4().hex[:12]}",
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    status="FILLED",
                    price=entry_price,
                    avg_price=entry_price,
                    quantity=quantity,
                    filled_qty=quantity,
                )

                # Create mock TP orders
                tp_orders = []
                exit_side = "SELL" if side in ("BUY", SignalSide.BUY) else "BUY"
                for i, tp_level in enumerate([tp_levels.tp1, tp_levels.tp2, tp_levels.tp3], 1):
                    tp_orders.append(
                        OrderResult(
                            order_id=f"paper_tp{i}_{uuid.uuid4().hex[:12]}",
                            symbol=symbol,
                            side=exit_side,
                            order_type="TAKE_PROFIT_MARKET",
                            status="NEW",
                            price=tp_level.price,
                            quantity=tp_level.quantity,
                        )
                    )

                # Create mock SL order
                sl_order = OrderResult(
                    order_id=f"paper_sl_{uuid.uuid4().hex[:12]}",
                    symbol=symbol,
                    side=exit_side,
                    order_type="STOP_MARKET",
                    status="NEW",
                    price=sl_price,
                )

                # Record trade in DB
                trade_id = await self._record_trade(
                    bot_id=bot_id,
                    signal=signal,
                    symbol=symbol,
                    side=side,
                    strategy_name=strategy_name,
                    leverage=leverage,
                    entry_price=entry_price,
                    quantity=quantity,
                    sl_price=sl_price,
                    tp_levels=tp_levels,
                    entry_order=entry_order,
                    slippage=0.0,
                )

                return TradeResult(
                    success=True,
                    trade_id=trade_id,
                    entry_price=entry_price,
                    entry_order=entry_order,
                    tp_orders=tp_orders,
                    sl_order=sl_order,
                    slippage=0.0,
                )

            # ── 1. Account setup ─────────────────────────────────
            await self._setup_account(symbol, leverage)

            # ── 2. Duplicate prevention ──────────────────────────
            existing = await self._get_open_orders(symbol)
            if any(o.get("type") == "MARKET" and o.get("status") == "NEW" for o in existing):
                return TradeResult(success=False, error=f"Duplicate market order detected for {symbol}")

            # ── 3. Market entry ──────────────────────────────────
            entry_order = await self._place_market_order(symbol, side, quantity)
            if entry_order is None:
                detail = getattr(self, '_last_order_error', '') or ''
                return TradeResult(success=False, error=f"Entry order failed for {symbol}: {detail}")

            entry_price = entry_order.avg_price or entry_order.price
            if entry_price <= 0:
                # Fetch from position as fallback
                entry_price = await self._get_entry_from_position(symbol) or 0.0

            # Slippage tracking
            requested_price = signal.entry_price if hasattr(signal, "entry_price") else entry_price
            slippage = abs(entry_price - requested_price) / requested_price if requested_price > 0 else 0.0

            # Recalibrate SL and TP relative to the actual fill price to prevent "would trigger immediately" errors
            if requested_price > 0 and entry_price > 0:
                price_shift = entry_price - requested_price
                sl_price += price_shift
                tp_levels.tp1.price += price_shift
                tp_levels.tp2.price += price_shift
                tp_levels.tp3.price += price_shift
                logger.info("Recalibrated SL/TP by %+f due to slippage (Filled: %f)", price_shift, entry_price)

            # ── 4. Protection orders (TP + SL) ───────────────────
            try:
                exit_side = "SELL" if side in ("BUY", SignalSide.BUY) else "BUY"
                tp_orders: list[OrderResult] = []

                # TP1
                tp1_order = await self._place_tp_order(
                    symbol, exit_side, tp_levels.tp1.quantity, tp_levels.tp1.price, "TP1"
                )
                if tp1_order:
                    tp_orders.append(tp1_order)

                # TP2
                tp2_order = await self._place_tp_order(
                    symbol, exit_side, tp_levels.tp2.quantity, tp_levels.tp2.price, "TP2"
                )
                if tp2_order:
                    tp_orders.append(tp2_order)

                # TP3
                tp3_order = await self._place_tp_order(
                    symbol, exit_side, tp_levels.tp3.quantity, tp_levels.tp3.price, "TP3"
                )
                if tp3_order:
                    tp_orders.append(tp3_order)

                # SL
                sl_order = await self._place_sl_order(symbol, exit_side, sl_price)
                if not sl_order:
                    err_msg = f"Stop Loss order rejected or failed. {self._last_order_error}"
                    logger.error("FATAL: Stop Loss order failed to place for %s. Executing emergency market close to prevent liquidation! Error: %s", symbol, self._last_order_error)
                    await self.close_position(symbol, quantity, "emergency_sl_failure")
                    return TradeResult(success=False, error=err_msg)

                # ── 5. Record in DB ──────────────────────────────────
                trade_id = await self._record_trade(
                    bot_id=bot_id,
                    signal=signal,
                    symbol=symbol,
                    side=side,
                    strategy_name=strategy_name,
                    leverage=leverage,
                    entry_price=entry_price,
                    quantity=quantity,
                    sl_price=sl_price,
                    tp_levels=tp_levels,
                    entry_order=entry_order,
                    slippage=slippage,
                )

                result = TradeResult(
                    success=True,
                    trade_id=trade_id,
                    entry_price=entry_price,
                    entry_order=entry_order,
                    tp_orders=tp_orders,
                    sl_order=sl_order,
                    slippage=round(slippage, 6),
                )

                logger.info(
                    "Trade executed: %s %s %s @ %.4f | SL=%.4f | slippage=%.4f%%",
                    side, quantity, symbol, entry_price, sl_price, slippage * 100,
                )
                return result
            except Exception as e:
                logger.error("FATAL: Unexpected error during protection order placement: %s. Executing emergency close!", e)
                await self.close_position(symbol, quantity, "emergency_unexpected_error")
                raise e

        except Exception as exc:
            logger.error("Trade execution failed: %s", exc, exc_info=True)
            return TradeResult(success=False, error=str(exc))

    # ─────────────────────────────────────────────────────────────
    #  POSITION MANAGEMENT
    # ─────────────────────────────────────────────────────────────

    async def close_position(self, symbol: str, quantity: float, reason: str = "manual") -> Optional[OrderResult]:
        """Close a position via market order."""
        if settings.is_paper:
            logger.info("[PAPER] Position closed for %s qty=%s reason=%s", symbol, quantity, reason)
            return OrderResult(
                order_id=f"paper_close_{uuid.uuid4().hex[:12]}",
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                status="FILLED",
                quantity=quantity,
                filled_qty=quantity,
            )
        try:
            # Determine current side from position
            positions = await self.get_open_positions()
            pos = next((p for p in positions if p["symbol"] == symbol and float(p.get("positionAmt", 0)) != 0), None)
            if pos is None:
                logger.warning("No open position found for %s", symbol)
                return None

            pos_amt = float(pos["positionAmt"])
            close_side = "SELL" if pos_amt > 0 else "BUY"
            close_qty = min(abs(pos_amt), quantity)

            # Cancel existing orders first
            await self.cancel_orders(symbol)

            order = await self._place_market_order(symbol, close_side, close_qty)
            if order:
                logger.info("Position closed: %s %s qty=%s reason=%s", close_side, symbol, close_qty, reason)
            return order

        except Exception as exc:
            logger.error("Failed to close position %s: %s", symbol, exc, exc_info=True)
            return None

    async def modify_sl(self, symbol: str, new_sl_price: float) -> Optional[OrderResult]:
        """
        Move the stop-loss to a new price.

        Cancels the old SL and places a new STOP_MARKET.
        """
        if settings.is_paper:
            logger.info("[PAPER] SL modified for %s to %.4f", symbol, new_sl_price)
            return OrderResult(
                order_id=f"paper_sl_{uuid.uuid4().hex[:12]}",
                symbol=symbol,
                side="SELL",
                order_type="STOP_MARKET",
                status="NEW",
                price=new_sl_price,
            )
        try:
            # Cancel existing SL
            open_orders = await self._get_open_orders(symbol)
            for order in open_orders:
                if order.get("type") in ("STOP_MARKET", "STOP"):
                    await self._cancel_order(symbol, order["orderId"])

            # Determine exit side
            positions = await self.get_open_positions()
            pos = next((p for p in positions if p["symbol"] == symbol and float(p.get("positionAmt", 0)) != 0), None)
            if pos is None:
                logger.warning("No position to modify SL for %s", symbol)
                return None

            pos_amt = float(pos["positionAmt"])
            exit_side = "SELL" if pos_amt > 0 else "BUY"

            new_order = await self._place_sl_order(symbol, exit_side, new_sl_price)
            if new_order:
                logger.info("SL modified: %s new_sl=%.4f", symbol, new_sl_price)
            return new_order

        except Exception as exc:
            logger.error("Failed to modify SL for %s: %s", symbol, exc, exc_info=True)
            return None

    async def cancel_orders(self, symbol: str) -> bool:
        """Cancel all open orders for a symbol."""
        if settings.is_paper:
            return True
        try:
            await self._ensure_client()
            await self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info("All open orders cancelled for %s", symbol)
            return True
        except Exception as exc:
            logger.error("Failed to cancel orders for %s: %s", symbol, exc)
            return False

    async def cancel_tp_orders(self, symbol: str) -> int:
        """Cancel only TAKE_PROFIT orders for a symbol. Returns count cancelled."""
        if settings.is_paper:
            return 0
        cancelled = 0
        try:
            orders = await self._get_open_orders(symbol)
            for order in orders:
                if order.get("type") in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT_LIMIT", "TAKE_PROFIT"):
                    await self._cancel_order(symbol, order["orderId"])
                    cancelled += 1
            if cancelled:
                logger.info("Cancelled %d TP orders for %s", cancelled, symbol)
            return cancelled
        except Exception as exc:
            logger.error("Failed to cancel TP orders for %s: %s", symbol, exc)
            return cancelled

    async def get_open_positions(self) -> list[dict]:
        """Fetch all open positions from Binance."""
        if settings.is_paper:
            return []
        try:
            await self._ensure_client()
            account = await self.client.futures_account()
            positions = [
                p for p in account.get("positions", [])
                if float(p.get("positionAmt", 0)) != 0
            ]
            return positions
        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc, exc_info=True)
            return []

    # ─────────────────────────────────────────────────────────────
    #  PRECISION HELPERS
    # ─────────────────────────────────────────────────────────────

    async def get_quantity_precision(self, symbol: str) -> int:
        """Get LOT_SIZE step precision for a symbol."""
        filters = await self._get_symbol_filters(symbol)
        for f in filters:
            if f["filterType"] == "LOT_SIZE":
                step_size = f["stepSize"]
                return self._precision_from_step(step_size)
        return 3

    async def get_price_precision(self, symbol: str) -> int:
        """Get PRICE_FILTER tick precision for a symbol."""
        filters = await self._get_symbol_filters(symbol)
        for f in filters:
            if f["filterType"] == "PRICE_FILTER":
                tick_size = f["tickSize"]
                return self._precision_from_step(tick_size)
        return 2

    async def round_quantity(self, symbol: str, qty: float) -> float:
        """Round quantity to valid LOT_SIZE step."""
        precision = await self.get_quantity_precision(symbol)
        if precision == 0:
            return int(qty)
        return round(float(qty), precision)

    async def round_price(self, symbol: str, price: float) -> float:
        """Round price to valid PRICE_FILTER tick."""
        precision = await self.get_price_precision(symbol)
        return round(float(price), precision)

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — ORDER PLACEMENT WITH RETRY
    # ─────────────────────────────────────────────────────────────

    async def _place_market_order(self, symbol: str, side: str, quantity: float) -> Optional[OrderResult]:
        """Place a market order with retry logic."""
        await self._ensure_client()
        rounded_qty = await self.round_quantity(symbol, quantity)
        if rounded_qty <= 0:
            logger.error("Quantity rounded to 0 for %s (raw_qty=%s) — check balance and precision", symbol, quantity)
            self._last_order_error = f"Quantity rounded to 0 (raw={quantity}) — check balance/precision"
            return None

        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("[EXEC] Attempt %d: MARKET %s %s qty=%s", attempt, side, symbol, rounded_qty)
                result = await self.client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=rounded_qty,
                )
                return self._parse_order(result)
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Market order attempt %d failed: %s", attempt, last_error)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        self._last_order_error = last_error
        return None

    async def _place_tp_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        label: str,
    ) -> Optional[OrderResult]:
        """Place a TAKE_PROFIT_MARKET order with retry."""
        await self._ensure_client()
        rounded_qty = await self.round_quantity(symbol, quantity)
        rounded_price = await self.round_price(symbol, stop_price)

        if rounded_qty <= 0:
            logger.warning("TP %s quantity rounded to 0 for %s — skipping", label, symbol)
            return None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "[EXEC] Attempt %d: %s TAKE_PROFIT_MARKET %s qty=%s @ %s",
                    attempt, label, symbol, rounded_qty, rounded_price,
                )
                result = await self.client.futures_create_algo_order(
                    algoType="CONDITIONAL",
                    symbol=symbol,
                    side=side,
                    type="TAKE_PROFIT_MARKET",
                    quantity=rounded_qty,
                    triggerPrice=rounded_price,
                    workingType=WORKING_TYPE,
                    reduceOnly="true",
                )
                return self._parse_order(result)
            except Exception as exc:
                logger.warning("%s order attempt %d failed: %s", label, attempt, exc)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        return None

    async def _place_sl_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
    ) -> Optional[OrderResult]:
        """Place a STOP_MARKET order with retry. Uses closePosition for full SL."""
        await self._ensure_client()
        rounded_price = await self.round_price(symbol, stop_price)

        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "[EXEC] Attempt %d: STOP_MARKET %s @ %s (closePosition)",
                    attempt, symbol, rounded_price,
                )
                result = await self.client.futures_create_algo_order(
                    algoType="CONDITIONAL",
                    symbol=symbol,
                    side=side,
                    type="STOP_MARKET",
                    triggerPrice=rounded_price,
                    closePosition="true",
                    workingType=WORKING_TYPE,
                )
                return self._parse_order(result)
            except Exception as exc:
                last_error = str(exc)
                logger.warning("SL order attempt %d failed: %s", attempt, exc)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        self._last_order_error = last_error
        return None

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — ACCOUNT SETUP
    # ─────────────────────────────────────────────────────────────

    async def _setup_account(self, symbol: str, leverage: int) -> None:
        """Set leverage and isolated margin for the symbol."""
        try:
            await self._ensure_client()
            await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as exc:
            logger.debug("Leverage set note for %s: %s", symbol, exc)

        try:
            await self.client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")
        except Exception as exc:
            # Margin type may already be set
            if "No need to change margin type" not in str(exc):
                logger.debug("Margin type note for %s: %s", symbol, exc)

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — ORDER & POSITION QUERIES
    # ─────────────────────────────────────────────────────────────

    async def _get_open_orders(self, symbol: str) -> list[dict]:
        """Fetch open orders for a symbol."""
        try:
            await self._ensure_client()
            return await self.client.futures_get_open_orders(symbol=symbol)
        except Exception as exc:
            logger.error("Failed to get open orders for %s: %s", symbol, exc)
            return []

    async def _cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel a single order by ID."""
        try:
            await self._ensure_client()
            await self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return True
        except Exception as exc:
            logger.warning("Failed to cancel order %s on %s: %s", order_id, symbol, exc)
            return False

    async def _get_entry_from_position(self, symbol: str) -> Optional[float]:
        """Fetch entry price from Binance position data."""
        try:
            positions = await self.get_open_positions()
            for p in positions:
                if p["symbol"] == symbol:
                    return float(p.get("entryPrice", 0))
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — EXCHANGE INFO CACHE
    # ─────────────────────────────────────────────────────────────

    async def _get_exchange_info(self) -> dict:
        """Fetch and cache exchange info (refreshes every 5 min)."""
        import time
        now = time.time()
        if self._exchange_info_cache is None or (now - self._exchange_info_ts) > 300:
            await self._ensure_client()
            self._exchange_info_cache = await self.client.futures_exchange_info()
            self._exchange_info_ts = now
        return self._exchange_info_cache

    async def _get_symbol_filters(self, symbol: str) -> list[dict]:
        """Get filters for a specific symbol."""
        info = await self._get_exchange_info()
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                return s.get("filters", [])
        return []

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — DB RECORDING
    # ─────────────────────────────────────────────────────────────

    async def _record_trade(
        self,
        bot_id: UUID,
        signal: Any,
        symbol: str,
        side: str,
        strategy_name: str,
        leverage: int,
        entry_price: float,
        quantity: float,
        sl_price: float,
        tp_levels: Any,
        entry_order: OrderResult,
        slippage: float,
    ) -> UUID:
        """Persist trade and position records to DB."""
        signal_id = None
        if hasattr(signal, "id"):
            signal_id = signal.id
        elif isinstance(signal, dict):
            signal_id = signal.get("id")

        trade_id = uuid.uuid4()
        trade = Trade(
            id=trade_id,
            bot_id=bot_id,
            signal_id=signal_id,
            symbol=symbol,
            side=SignalSide.BUY if side in ("BUY", SignalSide.BUY) else SignalSide.SELL,
            strategy_name=strategy_name,
            leverage=leverage,
            entry_price=entry_price,
            quantity=quantity,
            remaining_quantity=quantity,
            sl_price=sl_price,
            tp1_price=tp_levels.tp1.price,
            tp2_price=tp_levels.tp2.price,
            tp3_price=tp_levels.tp3.price,
            slippage=slippage,
            status=TradeStatus.OPEN,
            trade_state=TradeState.ENTRY,
            binance_order_id=entry_order.order_id,
            entry_time=datetime.utcnow(),
        )
        self.db.add(trade)

        position = Position(
            trade_id=trade_id,
            symbol=symbol,
            side=SignalSide.BUY if side in ("BUY", SignalSide.BUY) else SignalSide.SELL,
            quantity=quantity,
            mark_price=entry_price,
        )
        self.db.add(position)

        await self.db.commit()
        logger.info("Trade recorded in DB: id=%s symbol=%s", trade_id, symbol)
        return trade_id

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE — UTILITIES
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_order(raw: dict) -> OrderResult:
        """Parse Binance order response into OrderResult."""
        return OrderResult(
            order_id=str(raw.get("orderId", "")),
            client_order_id=str(raw.get("clientOrderId", "")),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            order_type=raw.get("type", ""),
            status=raw.get("status", ""),
            price=float(raw.get("price", 0)),
            avg_price=float(raw.get("avgPrice", 0)),
            quantity=float(raw.get("origQty", 0)),
            filled_qty=float(raw.get("executedQty", 0)),
            raw=raw,
        )

    @staticmethod
    def _precision_from_step(step: str) -> int:
        """Derive decimal precision from a step size string like '0.001'."""
        step_str = str(step)
        if "." not in step_str:
            return 0
        decimals = step_str.split(".")[-1].rstrip("0")
        return len(decimals)
