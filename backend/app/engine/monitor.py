"""
BinBot AI Auto Mode — Position Monitor
Async loop monitoring open positions every 5 seconds.

Responsibilities:
- Check TP1/TP2/TP3 hits and advance trade state
- Move SL to breakeven after TP1
- Trail SL to TP1 after TP2
- Close remaining after TP3
- Detect SL hits and record losses
- Sync Binance positions with DB
- Update unrealized PnL and mark price
- NEVER crash the bot loop
"""

import asyncio
import logging
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
    Log,
    LogLevel,
    LogSource,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────
MONITOR_INTERVAL_SECONDS: float = 5.0


class PositionMonitor:
    """
    Monitors open positions in a continuous async loop.

    For each open trade it checks TP/SL hits, advances the trade
    state machine, and keeps the DB in sync with Binance.
    """

    def __init__(
        self,
        client: Any = None,
        db_session: Optional[AsyncSession] = None,
        executor: Any = None,
        risk_manager: Any = None,
    ) -> None:
        """
        Args:
            client: ``binance.AsyncClient`` instance.
            db_session: SQLAlchemy async session.
            executor: ``TradeExecutor`` for order modifications.
            risk_manager: ``RiskManager`` for daily stats updates.
        """
        self.client = client
        self.db = db_session
        self.executor = executor
        self.risk = risk_manager
        self._running: bool = False

    async def _log_to_db(self, bot_id: UUID, level: LogLevel, source: LogSource, message: str) -> None:
        """Write a log entry to the database and broadcast to Socket.IO."""
        try:
            if self.db is not None:
                log = Log(
                    bot_id=bot_id,
                    level=level,
                    source=source,
                    message=message,
                )
                self.db.add(log)
                await self.db.commit()
            else:
                from app.db.session import async_session_factory
                async with async_session_factory() as session:
                    log = Log(
                        bot_id=bot_id,
                        level=level,
                        source=source,
                        message=message,
                    )
                    session.add(log)
                    await session.commit()
        except Exception as e:
            logger.error("Failed to write monitor log to DB: %s", e)

        # Also broadcast to dashboard
        try:
            from app.api.websocket import broadcast_log
            await broadcast_log(level.value, source.value, message)
        except Exception as e:
            logger.error("Failed to broadcast monitor log: %s", e)

    async def check_positions(
        self,
        bot_id: UUID,
        session: AsyncSession,
        executor: Any,
        risk_manager: Any,
        notifier: Any = None,
    ) -> None:
        """
        Single run of open positions check.
        Used by the main orchestrator loop.
        """
        self.db = session
        self.executor = executor
        self.risk = risk_manager
        self.notifier = notifier

        if not self.client and hasattr(executor, "client"):
            self.client = executor.client

        await self._ensure_client()
        await self._monitor_cycle(bot_id)

    async def _ensure_client(self) -> None:
        """Ensure client is initialized if needed."""
        if self.client is not None:
            return
        if self.executor and hasattr(self.executor, "client") and self.executor.client is not None:
            self.client = self.executor.client
            return

        if not settings.active_api_key:
            self.client = None
            return

        from binance import AsyncClient
        try:
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
        except Exception as exc:
            logger.error("Failed to create Binance client for monitor: %s", exc)
            self.client = None

    async def _get_mark_price_live(self, symbol: str, binance_positions: list[dict]) -> float:
        """Extract mark price for a symbol, falling back to Redis if in paper mode or list is empty."""
        for pos in binance_positions:
            if pos.get("symbol") == symbol:
                val = float(pos.get("markPrice", 0))
                if val > 0:
                    return val

        # Fallback to Redis
        try:
            from app.deps import get_redis
            redis = await get_redis()
            raw = await redis.get(f"binbot:market:mark:{symbol.upper()}")
            if raw:
                return float(raw)
        except Exception as e:
            logger.error("Failed to fetch mark price from Redis for %s: %s", symbol, e)
        return 0.0

    # ─────────────────────────────────────────────────────────────
    #  PUBLIC — MAIN LOOP
    # ─────────────────────────────────────────────────────────────

    async def monitor_positions(self, bot_id: UUID) -> None:
        """
        Main monitoring loop.  Runs until ``stop()`` is called.

        Checks all open positions for the given bot every
        ``MONITOR_INTERVAL_SECONDS``.
        """
        self._running = True
        logger.info("Position monitor started for bot %s", bot_id)

        while self._running:
            try:
                await self._monitor_cycle(bot_id)
            except Exception as exc:
                # NEVER crash the bot loop
                logger.error("Monitor cycle error (will retry): %s", exc, exc_info=True)

            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)

        logger.info("Position monitor stopped for bot %s", bot_id)

    def stop(self) -> None:
        """Signal the monitor loop to stop gracefully."""
        self._running = False
        logger.info("Position monitor stop requested")

    @property
    def is_running(self) -> bool:
        """Whether the monitor loop is active."""
        return self._running

    # ─────────────────────────────────────────────────────────────
    #  MONITOR CYCLE
    # ─────────────────────────────────────────────────────────────

    async def _monitor_cycle(self, bot_id: UUID) -> None:
        """Single monitoring pass over all open trades."""
        # Fetch live positions from Binance
        binance_positions = await self._fetch_binance_positions()

        # Fetch open trades from DB
        open_trades = await self._get_open_trades(bot_id)

        for trade in open_trades:
            try:
                await self._process_trade(trade, binance_positions)
            except Exception as exc:
                logger.error(
                    "Error processing trade %s (%s): %s",
                    trade.id, trade.symbol, exc, exc_info=True,
                )

        # Position sync: detect positions closed on Binance side
        await self._sync_positions(open_trades, binance_positions, bot_id)

    # ─────────────────────────────────────────────────────────────
    #  PER-TRADE PROCESSING
    # ─────────────────────────────────────────────────────────────

    async def _process_trade(self, trade: Trade, binance_positions: list[dict]) -> None:
        """
        Process a single open trade:
        - Update mark price and unrealized PnL
        - Check TP/SL hits and advance state
        """
        symbol = trade.symbol
        mark_price = await self._get_mark_price_live(symbol, binance_positions)
        if mark_price <= 0:
            return

        # Update position record
        await self._update_position(trade, mark_price)

        # State machine
        if trade.trade_state == TradeState.ENTRY:
            await self._check_tp1(trade, mark_price)
            await self._check_sl(trade, mark_price)

        elif trade.trade_state == TradeState.TP1_HIT:
            await self._check_tp2(trade, mark_price)
            await self._check_sl(trade, mark_price)

        elif trade.trade_state in (TradeState.BE_MOVED, TradeState.TP2_HIT):
            await self._check_tp3(trade, mark_price)
            await self._check_sl(trade, mark_price)

    # ─────────────────────────────────────────────────────────────
    #  TP / SL CHECKS
    # ─────────────────────────────────────────────────────────────

    async def _check_tp1(self, trade: Trade, mark_price: float) -> None:
        """Check if TP1 has been hit → move SL to breakeven."""
        if trade.tp1_price is None:
            return

        hit = (
            (trade.side == SignalSide.BUY and mark_price >= trade.tp1_price)
            or (trade.side == SignalSide.SELL and mark_price <= trade.tp1_price)
        )

        if hit:
            logger.info("TP1 hit for %s @ %.4f (target %.4f)", trade.symbol, mark_price, trade.tp1_price)

            # Move SL to breakeven
            await self.executor.modify_sl(trade.symbol, trade.entry_price)

            # Update remaining quantity
            tp1_qty = round(trade.quantity * settings.TP1_CLOSE_PCT, 8)
            trade.remaining_quantity = round(trade.remaining_quantity - tp1_qty, 8)
            trade.trade_state = TradeState.TP1_HIT
            trade.status = TradeStatus.PARTIAL_TP

            await self.db.commit()
            logger.info(
                "Trade %s → TP1_HIT | SL moved to BE (%.4f) | remaining_qty=%.4f",
                trade.id, trade.entry_price, trade.remaining_quantity,
            )
            prefix = "[PAPER] " if settings.is_paper else ""
            await self._log_to_db(
                trade.bot_id,
                LogLevel.TRADE,
                LogSource.MONITOR,
                f"🎯 {prefix}TP1 HIT | {trade.symbol} | Closed {settings.TP1_CLOSE_PCT * 100:.0f}% ({tp1_qty}) @ {mark_price:.4f} | SL moved to Breakeven ({trade.entry_price:.4f})"
            )

    async def _check_tp2(self, trade: Trade, mark_price: float) -> None:
        """Check if TP2 has been hit → trail SL to TP1 level."""
        if trade.tp2_price is None:
            return

        hit = (
            (trade.side == SignalSide.BUY and mark_price >= trade.tp2_price)
            or (trade.side == SignalSide.SELL and mark_price <= trade.tp2_price)
        )

        if hit:
            logger.info("TP2 hit for %s @ %.4f (target %.4f)", trade.symbol, mark_price, trade.tp2_price)

            # Trail SL to TP1 level
            if trade.tp1_price:
                await self.executor.modify_sl(trade.symbol, trade.tp1_price)

            # Update remaining quantity
            tp2_qty = round(trade.quantity * settings.TP2_CLOSE_PCT, 8)
            trade.remaining_quantity = round(trade.remaining_quantity - tp2_qty, 8)
            trade.trade_state = TradeState.TP2_HIT

            await self.db.commit()
            logger.info(
                "Trade %s → TP2_HIT | SL trailed to TP1 (%.4f) | remaining_qty=%.4f",
                trade.id, trade.tp1_price, trade.remaining_quantity,
            )
            prefix = "[PAPER] " if settings.is_paper else ""
            await self._log_to_db(
                trade.bot_id,
                LogLevel.TRADE,
                LogSource.MONITOR,
                f"🎯 {prefix}TP2 HIT | {trade.symbol} | Closed {settings.TP2_CLOSE_PCT * 100:.0f}% ({tp2_qty}) @ {mark_price:.4f} | SL trailed to TP1 ({trade.tp1_price:.4f})"
            )

    async def _check_tp3(self, trade: Trade, mark_price: float) -> None:
        """Check if TP3 has been hit → close remaining and finalize."""
        if trade.tp3_price is None:
            return

        hit = (
            (trade.side == SignalSide.BUY and mark_price >= trade.tp3_price)
            or (trade.side == SignalSide.SELL and mark_price <= trade.tp3_price)
        )

        if hit:
            logger.info("TP3 hit for %s @ %.4f (target %.4f)", trade.symbol, mark_price, trade.tp3_price)

            # Close remaining position
            if trade.remaining_quantity > 0:
                await self.executor.close_position(trade.symbol, trade.remaining_quantity, reason="TP3")

            # Finalize trade
            pnl = self._calculate_pnl(trade, mark_price)
            trade.trade_state = TradeState.TP3_HIT
            trade.status = TradeStatus.CLOSED
            trade.exit_price = mark_price
            trade.exit_time = datetime.utcnow()
            trade.realized_pnl = pnl
            trade.remaining_quantity = 0.0
            trade.close_reason = "TP3"

            await self.db.commit()

            # Update risk stats
            await self.risk.update_daily_stats(trade.bot_id, pnl)

            logger.info("Trade %s CLOSED via TP3 | PnL=%.2f", trade.id, pnl)
            prefix = "[PAPER] " if settings.is_paper else ""
            await self._log_to_db(
                trade.bot_id,
                LogLevel.TRADE,
                LogSource.MONITOR,
                f"💰 {prefix}TRADE CLOSED (TP3 Hit) | {trade.symbol} | Remaining closed @ {mark_price:.4f} | Realized PnL: ${pnl:.2f}"
            )

    async def _check_sl(self, trade: Trade, mark_price: float) -> None:
        """Check if SL has been hit → close and record loss."""
        sl_price = trade.sl_price
        if sl_price is None or sl_price <= 0:
            return

        # Current effective SL depends on state
        effective_sl = sl_price
        if trade.trade_state == TradeState.TP1_HIT:
            # SL was moved to breakeven
            effective_sl = trade.entry_price
        elif trade.trade_state == TradeState.TP2_HIT and trade.tp1_price:
            # SL was trailed to TP1
            effective_sl = trade.tp1_price

        hit = (
            (trade.side == SignalSide.BUY and mark_price <= effective_sl)
            or (trade.side == SignalSide.SELL and mark_price >= effective_sl)
        )

        if hit:
            logger.info(
                "SL hit for %s @ %.4f (effective SL %.4f)",
                trade.symbol, mark_price, effective_sl,
            )

            # Close remaining
            if trade.remaining_quantity > 0:
                await self.executor.close_position(trade.symbol, trade.remaining_quantity, reason="SL")

            pnl = self._calculate_pnl(trade, mark_price)
            trade.trade_state = TradeState.SL_HIT
            trade.status = TradeStatus.CLOSED
            trade.exit_price = mark_price
            trade.exit_time = datetime.utcnow()
            trade.realized_pnl = pnl
            trade.remaining_quantity = 0.0
            trade.close_reason = "SL"

            await self.db.commit()

            await self.risk.update_daily_stats(trade.bot_id, pnl)

            logger.info("Trade %s CLOSED via SL | PnL=%.2f", trade.id, pnl)
            prefix = "[PAPER] " if settings.is_paper else ""
            await self._log_to_db(
                trade.bot_id,
                LogLevel.TRADE,
                LogSource.MONITOR,
                f"🛑 {prefix}TRADE CLOSED (SL Hit) | {trade.symbol} | Closed @ {mark_price:.4f} (effective SL: {effective_sl:.4f}) | Realized PnL: ${pnl:.2f}"
            )

    # ─────────────────────────────────────────────────────────────
    #  POSITION SYNC
    # ─────────────────────────────────────────────────────────────

    async def _sync_positions(
        self,
        db_trades: list[Trade],
        binance_positions: list[dict],
        bot_id: UUID,
    ) -> None:
        """
        Detect positions that were closed on Binance but still open in DB.

        This handles cases where TP/SL filled on Binance and the
        monitor missed the exact moment.
        """
        binance_symbols = {
            p["symbol"] for p in binance_positions
            if float(p.get("positionAmt", 0)) != 0
        }

        for trade in db_trades:
            if trade.symbol not in binance_symbols:
                logger.warning(
                    "Position sync: %s (%s) closed on Binance but open in DB — closing",
                    trade.symbol, trade.id,
                )

                # Estimate PnL from last known mark price
                position = await self._get_position_record(trade.id)
                exit_price = position.mark_price if position and position.mark_price > 0 else trade.entry_price
                pnl = self._calculate_pnl(trade, exit_price)

                trade.status = TradeStatus.CLOSED
                trade.exit_price = exit_price
                trade.exit_time = datetime.utcnow()
                trade.realized_pnl = pnl
                trade.remaining_quantity = 0.0
                trade.close_reason = "sync"

                if trade.trade_state == TradeState.ENTRY:
                    trade.trade_state = TradeState.SL_HIT

                await self.db.commit()
                await self.risk.update_daily_stats(trade.bot_id, pnl)
                logger.info("Synced trade %s as closed | PnL=%.2f", trade.id, pnl)
                prefix = "[PAPER] " if settings.is_paper else ""
                await self._log_to_db(
                    bot_id,
                    LogLevel.WARNING,
                    LogSource.MONITOR,
                    f"⚠️ {prefix}POSITION SYNC | {trade.symbol} was closed on Binance (likely SL/TP fill) | Synced DB to CLOSED @ {exit_price:.4f} | Realized PnL: ${pnl:.2f}"
                )

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────

    async def _get_open_trades(self, bot_id: UUID) -> list[Trade]:
        """Fetch all open/partial_tp trades for a bot."""
        result = await self.db.execute(
            select(Trade).where(
                and_(
                    Trade.bot_id == bot_id,
                    Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
                )
            )
        )
        return list(result.scalars().all())

    async def _fetch_binance_positions(self) -> list[dict]:
        """Fetch positions from Binance with error handling."""
        if self.client is None:
            return []
        try:
            account = await self.client.futures_account()
            return account.get("positions", [])
        except Exception as exc:
            logger.error("Failed to fetch Binance positions: %s", exc)
            return []

    async def _update_position(self, trade: Trade, mark_price: float) -> None:
        """Update the Position record with current mark price and unrealized PnL."""
        try:
            position = await self._get_position_record(trade.id)
            if position is None:
                return

            position.mark_price = mark_price

            # Calculate unrealized PnL
            if trade.side == SignalSide.BUY:
                unrealized = (mark_price - trade.entry_price) * trade.remaining_quantity
            else:
                unrealized = (trade.entry_price - mark_price) * trade.remaining_quantity

            position.unrealized_pnl = round(unrealized, 4)
            position.quantity = trade.remaining_quantity
            position.updated_at = datetime.utcnow()

            await self.db.commit()
        except Exception as exc:
            logger.error("Failed to update position for trade %s: %s", trade.id, exc)
            await self.db.rollback()

    async def _get_position_record(self, trade_id: UUID) -> Optional[Position]:
        """Fetch the Position record linked to a trade."""
        result = await self.db.execute(
            select(Position).where(Position.trade_id == trade_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _get_mark_price(symbol: str, binance_positions: list[dict]) -> float:
        """Extract mark price for a symbol from Binance position data."""
        for pos in binance_positions:
            if pos.get("symbol") == symbol:
                return float(pos.get("markPrice", 0))
        return 0.0

    @staticmethod
    def _calculate_pnl(trade: Trade, exit_price: float) -> float:
        """
        Calculate realized PnL for remaining quantity.

        Includes already-realized PnL from partial TP closes.
        """
        if trade.entry_price <= 0:
            return 0.0

        if trade.side == SignalSide.BUY:
            raw_pnl = (exit_price - trade.entry_price) * trade.remaining_quantity
        else:
            raw_pnl = (trade.entry_price - exit_price) * trade.remaining_quantity

        # Add any previously realized PnL from partial closes
        total_pnl = trade.realized_pnl + raw_pnl
        return round(total_pnl, 4)
