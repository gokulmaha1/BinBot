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
        client: Any,
        db_session: AsyncSession,
        executor: Any,
        risk_manager: Any,
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
        # Fetch open trades from DB
        open_trades = await self._get_open_trades(bot_id)
        if not open_trades:
            return

        # Fetch live positions from Binance
        binance_positions = await self._fetch_binance_positions()

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
        mark_price = self._get_mark_price(symbol, binance_positions)
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
