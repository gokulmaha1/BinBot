"""
BinBot AI Auto Mode — Risk Management Engine
CAPITAL PRESERVATION IS PRIORITY #1.

All limits are HARDCODED from settings — never overridable.
Blocks: martingale, averaging down, revenge trading.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import numpy as np
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Bot,
    Trade,
    Position,
    TradeStatus,
    TradeState,
    SignalSide,
)

logger = logging.getLogger(__name__)


# ── Result Dataclasses ───────────────────────────────────────────

@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of a pre-trade risk check."""
    allowed: bool
    reason: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PositionSize:
    """Calculated position sizing."""
    quantity: float
    leverage: int
    risk_amount: float
    risk_pct: float
    notional_value: float = 0.0


@dataclass(frozen=True)
class TPLevel:
    """Single take-profit level."""
    price: float
    close_pct: float
    quantity: float
    rr_ratio: float


@dataclass(frozen=True)
class TPLevels:
    """All three TP tiers."""
    tp1: TPLevel
    tp2: TPLevel
    tp3: TPLevel


class RiskManager:
    """
    Pre-trade and in-trade risk gating.

    Every public method enforces the hardcoded limits from
    ``app.config.settings``.  No caller can bypass them.
    """

    def __init__(self, db_session: Optional[AsyncSession] = None, redis: Optional[object] = None) -> None:
        self.db = db_session
        self.redis = redis

    # ─────────────────────────────────────────────────────────────
    #  PRE-TRADE VALIDATION
    # ─────────────────────────────────────────────────────────────

    async def check_trade_allowed(
        self,
        bot_id: Optional[UUID] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        features: Optional[dict] = None,
        bot: Optional[Bot] = None,
        session: Optional[AsyncSession] = None,
    ) -> RiskCheckResult:
        """
        Run the full pre-trade risk gate.

        Checks (in order):
        1. Max active positions
        2. Daily loss limit
        3. Max drawdown
        4. Consecutive-loss cooldown
        5. Max trades per day
        6. Correlation with existing positions
        7. Revenge-trading detection
        8. Martingale / averaging-down block

        Returns ``RiskCheckResult`` with allowed flag and reason.
        """
        warnings: list[str] = []

        try:
            if session is not None:
                self.db = session
            if bot_id is None and bot is not None:
                bot_id = bot.id
            if bot is None:
                if bot_id is None:
                    return RiskCheckResult(allowed=False, reason="bot or bot_id must be provided")
                bot = await self._get_bot(bot_id)
            if bot is None:
                return RiskCheckResult(allowed=False, reason="Bot not found")

            # ── Reset daily stats if new day ─────────────────────
            await self._maybe_reset_daily(bot)

            # ── 1. Max active positions ──────────────────────────
            active_count = await self._count_active_positions(bot_id)
            if active_count >= settings.MAX_ACTIVE_POSITIONS:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Max active positions reached ({active_count}/{settings.MAX_ACTIVE_POSITIONS})",
                )

            # ── 2. Daily loss limit ──────────────────────────────
            if bot.daily_starting_equity > 0:
                daily_loss_pct = abs(bot.daily_pnl) / bot.daily_starting_equity if bot.daily_pnl < 0 else 0.0
                if daily_loss_pct >= settings.MAX_DAILY_LOSS:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"Daily loss limit hit ({daily_loss_pct:.2%} >= {settings.MAX_DAILY_LOSS:.0%})",
                    )
                if daily_loss_pct >= settings.MAX_DAILY_LOSS * 0.8:
                    warnings.append(f"Approaching daily loss limit ({daily_loss_pct:.2%})")

            # ── 3. Max drawdown ──────────────────────────────────
            if bot.peak_equity > 0 and bot.daily_starting_equity > 0:
                current_equity = bot.daily_starting_equity + bot.daily_pnl
                drawdown = (bot.peak_equity - current_equity) / bot.peak_equity
                if drawdown >= settings.MAX_DRAWDOWN:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"Max drawdown breached ({drawdown:.2%} >= {settings.MAX_DRAWDOWN:.0%})",
                    )
                if drawdown >= settings.MAX_DRAWDOWN * 0.75:
                    warnings.append(f"Approaching max drawdown ({drawdown:.2%})")

            # ── 4. Consecutive-loss cooldown ─────────────────────
            if bot.consecutive_losses >= settings.MAX_CONSECUTIVE_LOSSES:
                if bot.cooldown_until and datetime.utcnow() < bot.cooldown_until:
                    remaining = (bot.cooldown_until - datetime.utcnow()).total_seconds()
                    return RiskCheckResult(
                        allowed=False,
                        reason=(
                            f"Cooldown active after {bot.consecutive_losses} consecutive losses. "
                            f"Resumes in {remaining / 60:.0f} min"
                        ),
                    )

            # ── 5. Max trades per day ────────────────────────────
            if bot.trades_today >= settings.MAX_TRADES_PER_DAY:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Max daily trades reached ({bot.trades_today}/{settings.MAX_TRADES_PER_DAY})",
                )

            # ── 6. Correlation check ─────────────────────────────
            existing_symbols = await self._get_open_symbols(bot_id)
            if existing_symbols:
                corr = await self.check_correlation(symbol, existing_symbols)
                correlated_count = sum(1 for c in corr.values() if c >= settings.CORRELATION_THRESHOLD)
                if correlated_count >= settings.MAX_CORRELATED_POSITIONS:
                    most_correlated = max(corr, key=corr.get)
                    return RiskCheckResult(
                        allowed=False,
                        reason=(
                            f"Too many correlated positions. "
                            f"{symbol} correlates {corr[most_correlated]:.2f} with {most_correlated}"
                        ),
                    )
                # Warn on moderately correlated
                for sym, c in corr.items():
                    if c >= 0.70:
                        warnings.append(f"Moderate correlation with {sym}: {c:.2f}")

            # ── 7. Revenge-trading detection ─────────────────────
            is_revenge = await self._check_revenge_trade(bot_id, symbol)
            if is_revenge:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Revenge trade blocked: {symbol} was closed at a loss recently",
                )

            # ── 8. Averaging-down / martingale block ─────────────
            has_open = await self._has_open_position(bot_id, symbol)
            if has_open:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Averaging down blocked: already have an open position on {symbol}",
                )

            return RiskCheckResult(allowed=True, reason="All risk checks passed", warnings=warnings)

        except Exception as exc:
            logger.error("Risk check failed: %s", exc, exc_info=True)
            return RiskCheckResult(allowed=False, reason=f"Risk check error: {exc}")

    # ─────────────────────────────────────────────────────────────
    #  POSITION SIZING
    # ─────────────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        sl_distance: float,
        symbol: str,
        qty_precision: int = 3,
    ) -> PositionSize:
        """
        Calculate position size based on 1% risk per trade.

        ``sl_distance`` is the absolute price distance to the stop-loss
        (always positive).
        """
        if sl_distance <= 0 or entry_price <= 0 or equity <= 0:
            reason = (
                "equity<=0" if equity <= 0 else
                "entry_price<=0" if entry_price <= 0 else
                "sl_distance<=0"
            )
            logger.error("Invalid inputs for position sizing: %s equity=%.2f entry=%.6f sl_dist=%.6f",
                         reason, equity, entry_price, sl_distance)
            return PositionSize(quantity=0.0, leverage=1, risk_amount=0.0, risk_pct=0.0)

        # Enforce a minimum Stop Loss percentage (0.5%) to prevent massive quantities from micro-ATRs
        min_sl_distance = entry_price * 0.005
        if sl_distance < min_sl_distance:
            logger.info("SL distance %.6f too tight, widening to minimum %.6f (0.5%%)", sl_distance, min_sl_distance)
            sl_distance = min_sl_distance

        # Risk amount: % of equity
        risk_amount = equity * settings.MAX_RISK_PER_TRADE

        # Raw quantity from risk
        quantity = risk_amount / sl_distance

        # Notional value check
        notional_value = quantity * entry_price

        # Calculate safe leverage limit: liquidation MUST be further away than SL
        # Liquidation roughly happens at 1 / leverage. So max leverage is ~1 / sl_pct. 
        # We multiply by 0.8 to leave a 20% safety buffer for maintenance margin.
        sl_pct = sl_distance / entry_price
        safe_leverage_limit = max(1, int((1.0 / sl_pct) * 0.8))
        
        # Use target leverage (MAX_LEVERAGE) but cap it at safe_leverage_limit
        target_leverage = min(settings.MAX_LEVERAGE, safe_leverage_limit)
        
        max_capital = equity * settings.CAPITAL_PER_TRADE_PCT
        required_margin = notional_value / target_leverage if target_leverage > 0 else notional_value

        # If required margin exceeds allocated capital, reduce position size
        if required_margin > max_capital:
            scale = max_capital / required_margin
            quantity *= scale
            notional_value = quantity * entry_price
            required_margin = notional_value / target_leverage
            actual_risk = (quantity * sl_distance) / equity
            logger.warning(
                "Position size reduced: margin $%.2f > cap $%.2f. Risk: %.2f%%",
                required_margin, max_capital, actual_risk * 100,
            )

        leverage = target_leverage

        # Binance minimum notional check ($5 USDT for most futures pairs)
        MIN_NOTIONAL = 5.0
        if notional_value < MIN_NOTIONAL:
            # Small-account fallback: try to size to minimum notional using full buying power
            target_notional = MIN_NOTIONAL * 2  # $10 target for some buffer
            fallback_quantity = target_notional / entry_price
            fallback_margin = target_notional / target_leverage

            if fallback_margin <= max_capital and fallback_quantity > 0:
                quantity = fallback_quantity
                notional_value = target_notional
                required_margin = fallback_margin
                logger.warning(
                    "Small-account fallback for %s: sized to $%.0f notional (margin=$%.2f cap=$%.2f)",
                    symbol, target_notional, fallback_margin, max_capital,
                )
            else:
                logger.warning(
                    "Notional $%.2f below minimum $%.0f for %s — "
                    "equity=%.2f entry=%.6f sl_dist=%.6f qty=%.4f margin_needed=%.2f cap=%.2f",
                    notional_value, MIN_NOTIONAL, symbol,
                    equity, entry_price, sl_distance, quantity,
                    fallback_margin, max_capital,
                )
                return PositionSize(quantity=0.0, leverage=1, risk_amount=0.0, risk_pct=0.0)

        # Apply Binance precision
        quantity = round(quantity, qty_precision)
        risk_pct = (quantity * sl_distance) / equity if equity > 0 else 0.0

        result = PositionSize(
            quantity=quantity,
            leverage=leverage,
            risk_amount=round(risk_amount, 2),
            risk_pct=round(risk_pct, 4),
            notional_value=round(notional_value, 2),
        )

        logger.info(
            "Position size: qty=%s lev=%dx risk=$%.2f (%.2f%%) notional=$%.2f",
            result.quantity,
            result.leverage,
            result.risk_amount,
            result.risk_pct * 100,
            result.notional_value,
        )
        return result

    # ─────────────────────────────────────────────────────────────
    #  TAKE PROFIT LEVELS
    # ─────────────────────────────────────────────────────────────

    def calculate_tp_levels(
        self,
        entry_price: float,
        sl_distance: float,
        side: str,
        leverage: int = 1,
        total_quantity: float = 1.0,
        price_precision: int = 2,
        qty_precision: int = 3,
    ) -> TPLevels:
        """
        Calculate three take-profit levels based on Risk:Reward.

        TP1 = 1:1 R:R → close 40%
        TP2 = 1:2 R:R → close 30%
        TP3 = 1:3 R:R → close remaining 30%

        All ratios sourced from settings.
        """
        direction = 1.0 if side == SignalSide.BUY.value or side == SignalSide.BUY else -1.0

        if getattr(settings, "TP_ROI_TARGET", None):
            # Calculate TP distance required to hit the exact ROI %
            # ROI = (price_change / entry) * leverage * 100
            # price_change = (ROI / 100) / leverage * entry
            required_price_move = (settings.TP_ROI_TARGET / 100.0) / leverage * entry_price
            tp1_price = round(entry_price + direction * required_price_move, price_precision)
            tp2_price = tp1_price
            tp3_price = tp1_price
            logger.info("Using Fixed ROI Target: %.2f%%. Calculated TP=%f", settings.TP_ROI_TARGET, tp1_price)
        else:
            tp1_price = round(entry_price + direction * sl_distance * settings.TP1_RATIO, price_precision)
            tp2_price = round(entry_price + direction * sl_distance * settings.TP2_RATIO, price_precision)
            tp3_price = round(entry_price + direction * sl_distance * settings.TP3_RATIO, price_precision)

        tp1_qty = round(total_quantity * settings.TP1_CLOSE_PCT, qty_precision)
        tp2_qty = round(total_quantity * settings.TP2_CLOSE_PCT, qty_precision)
        tp3_qty = round(total_quantity - tp1_qty - tp2_qty, qty_precision)  # remainder

        return TPLevels(
            tp1=TPLevel(price=tp1_price, close_pct=settings.TP1_CLOSE_PCT, quantity=tp1_qty, rr_ratio=settings.TP1_RATIO),
            tp2=TPLevel(price=tp2_price, close_pct=settings.TP2_CLOSE_PCT, quantity=tp2_qty, rr_ratio=settings.TP2_RATIO),
            tp3=TPLevel(price=tp3_price, close_pct=settings.TP3_CLOSE_PCT, quantity=tp3_qty, rr_ratio=settings.TP3_RATIO),
        )

    # ─────────────────────────────────────────────────────────────
    #  DAILY STATS UPDATE
    # ─────────────────────────────────────────────────────────────

    async def update_daily_stats(self, bot_id: UUID, pnl: float) -> None:
        """
        Update the bot's running daily PnL and consecutive-loss tracker.

        Called after every trade close.
        """
        try:
            bot = await self._get_bot(bot_id)
            if bot is None:
                return

            bot.daily_pnl += pnl
            bot.trades_today += 1

            if pnl < 0:
                bot.consecutive_losses += 1
                if bot.consecutive_losses >= settings.MAX_CONSECUTIVE_LOSSES:
                    bot.cooldown_until = datetime.utcnow() + timedelta(
                        seconds=settings.CONSECUTIVE_LOSS_COOLDOWN
                    )
                    logger.warning(
                        "Bot %s entering cooldown after %d consecutive losses. Resumes at %s",
                        bot_id,
                        bot.consecutive_losses,
                        bot.cooldown_until,
                    )
            else:
                bot.consecutive_losses = 0
                bot.cooldown_until = None

            # Update peak equity
            current_equity = bot.daily_starting_equity + bot.daily_pnl
            if current_equity > bot.peak_equity:
                bot.peak_equity = current_equity

            await self.db.commit()

            logger.info(
                "Daily stats updated: bot=%s pnl=%.2f daily_total=%.2f consec_losses=%d",
                bot_id, pnl, bot.daily_pnl, bot.consecutive_losses,
            )
        except Exception as exc:
            logger.error("Failed to update daily stats: %s", exc, exc_info=True)
            await self.db.rollback()

    # ─────────────────────────────────────────────────────────────
    #  CORRELATION CHECK
    # ─────────────────────────────────────────────────────────────

    async def check_correlation(
        self,
        symbol: str,
        existing_symbols: list[str],
    ) -> dict[str, float]:
        """
        Compute Pearson correlation of ``symbol`` vs each open symbol
        using cached kline data from Redis.

        Falls back to a static correlation map for common crypto pairs
        when live data is unavailable.
        """
        correlations: dict[str, float] = {}

        for existing in existing_symbols:
            if existing == symbol:
                continue
            try:
                corr = await self._compute_pair_correlation(symbol, existing)
                correlations[existing] = corr
            except Exception as exc:
                logger.warning("Correlation calc failed for %s vs %s: %s", symbol, existing, exc)
                # Use static fallback
                correlations[existing] = self._static_correlation(symbol, existing)

        return correlations

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────

    async def _get_bot(self, bot_id: UUID) -> Optional[Bot]:
        """Fetch bot record."""
        result = await self.db.execute(select(Bot).where(Bot.id == bot_id))
        return result.scalar_one_or_none()

    async def _count_active_positions(self, bot_id: UUID) -> int:
        """Count trades in OPEN or PARTIAL_TP state."""
        result = await self.db.execute(
            select(func.count(Trade.id)).where(
                and_(
                    Trade.bot_id == bot_id,
                    Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
                )
            )
        )
        return result.scalar() or 0

    async def _get_open_symbols(self, bot_id: UUID) -> list[str]:
        """Return symbols of currently open trades."""
        result = await self.db.execute(
            select(Trade.symbol).where(
                and_(
                    Trade.bot_id == bot_id,
                    Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
                )
            )
        )
        return [row[0] for row in result.all()]

    async def _has_open_position(self, bot_id: UUID, symbol: str) -> bool:
        """Check if bot already has an open position on this symbol."""
        result = await self.db.execute(
            select(func.count(Trade.id)).where(
                and_(
                    Trade.bot_id == bot_id,
                    Trade.symbol == symbol,
                    Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
                )
            )
        )
        return (result.scalar() or 0) > 0

    async def _check_revenge_trade(self, bot_id: UUID, symbol: str) -> bool:
        """
        Detect revenge trading: same symbol closed at a loss within the
        last 30 minutes.
        """
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        result = await self.db.execute(
            select(func.count(Trade.id)).where(
                and_(
                    Trade.bot_id == bot_id,
                    Trade.symbol == symbol,
                    Trade.status == TradeStatus.CLOSED,
                    Trade.realized_pnl < 0,
                    Trade.exit_time >= cutoff,
                )
            )
        )
        return (result.scalar() or 0) > 0

    async def _maybe_reset_daily(self, bot: Bot) -> None:
        """Reset daily counters if the date has rolled over."""
        today = datetime.utcnow().date()
        if bot.last_reset_date != today:
            bot.daily_pnl = 0.0
            bot.trades_today = 0
            bot.last_reset_date = today
            await self.db.commit()
            logger.info("Daily stats reset for bot %s", bot.id)

    async def _compute_pair_correlation(self, sym_a: str, sym_b: str) -> float:
        """
        Compute 1h close-price Pearson correlation from Redis kline cache.
        Returns 0.0 if data is unavailable.
        """
        if self.redis is None:
            return self._static_correlation(sym_a, sym_b)

        try:
            import json
            key_a = f"klines:{sym_a}:1h"
            key_b = f"klines:{sym_b}:1h"
            raw_a = await self.redis.get(key_a)
            raw_b = await self.redis.get(key_b)

            if not raw_a or not raw_b:
                return self._static_correlation(sym_a, sym_b)

            closes_a = np.array([float(c[4]) for c in json.loads(raw_a)][-100:])
            closes_b = np.array([float(c[4]) for c in json.loads(raw_b)][-100:])

            min_len = min(len(closes_a), len(closes_b))
            if min_len < 20:
                return self._static_correlation(sym_a, sym_b)

            corr = float(np.corrcoef(closes_a[-min_len:], closes_b[-min_len:])[0, 1])
            return round(abs(corr), 4)
        except Exception:
            return self._static_correlation(sym_a, sym_b)

    @staticmethod
    def _static_correlation(sym_a: str, sym_b: str) -> float:
        """
        Static correlation fallback for common crypto pairs.

        Conservative — assumes moderate correlation when unknown.
        """
        HIGH_CORR_GROUPS: list[set[str]] = [
            {"BTCUSDT", "BTCDOMUSDT"},
            {"ETHUSDT", "ETHBTC"},
            {"SOLUSDT", "AVAXUSDT"},
            {"DOGEUSDT", "SHIBUSDT"},
            {"LINKUSDT", "DOTUSDT"},
            {"APTUSDT", "SUIUSDT"},
        ]
        for group in HIGH_CORR_GROUPS:
            if sym_a in group and sym_b in group:
                return 0.90
        # Default moderate correlation for any two crypto assets
        return 0.50
