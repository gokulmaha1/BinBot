"""
BinBot AI Auto Mode — Analytics API
Performance metrics, equity curves, and strategy analytics.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_

from app.deps import get_session, get_current_user
from app.models import (
    Bot, Trade, TradeStatus, PerformanceMetric, Signal, Strategy
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


class OverviewStats(BaseModel):
    balance: float
    today_pnl: float
    monthly_pnl: float
    total_pnl: float
    win_rate: float
    total_trades: int
    sharpe_ratio: float | None
    profit_factor: float | None
    max_drawdown: float
    active_positions: int
    bot_status: str
    trading_mode: str


class DailyPerformance(BaseModel):
    date: str
    pnl: float
    pnl_pct: float
    trades: int
    win_rate: float
    drawdown: float


class StrategyPerformance(BaseModel):
    name: str
    type: str
    total_trades: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


class EquityPoint(BaseModel):
    date: str
    equity: float


@router.get("/overview", response_model=OverviewStats)
async def get_overview(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get dashboard overview statistics."""
    from app.config import settings

    bot_result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = bot_result.scalar_one_or_none()

    if not bot:
        return OverviewStats(
            balance=0, today_pnl=0, monthly_pnl=0, total_pnl=0,
            win_rate=0, total_trades=0, sharpe_ratio=None,
            profit_factor=None, max_drawdown=0, active_positions=0,
            bot_status="idle", trading_mode=settings.TRADING_MODE.value,
        )

    # Total PnL
    total_pnl_result = await db.execute(
        select(func.sum(Trade.realized_pnl))
        .where(and_(Trade.bot_id == bot.id, Trade.status == TradeStatus.CLOSED))
    )
    total_pnl = total_pnl_result.scalar() or 0

    # Monthly PnL
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    monthly_pnl_result = await db.execute(
        select(func.sum(Trade.realized_pnl))
        .where(and_(
            Trade.bot_id == bot.id,
            Trade.status == TradeStatus.CLOSED,
            Trade.exit_time >= month_start,
        ))
    )
    monthly_pnl = monthly_pnl_result.scalar() or 0

    # Win rate
    total_count = await db.execute(
        select(func.count(Trade.id))
        .where(and_(Trade.bot_id == bot.id, Trade.status == TradeStatus.CLOSED))
    )
    total_trades = total_count.scalar() or 0

    win_count = await db.execute(
        select(func.count(Trade.id))
        .where(and_(
            Trade.bot_id == bot.id,
            Trade.status == TradeStatus.CLOSED,
            Trade.realized_pnl > 0,
        ))
    )
    winning = win_count.scalar() or 0

    # Active positions
    active_result = await db.execute(
        select(func.count(Trade.id))
        .where(and_(
            Trade.bot_id == bot.id,
            Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
        ))
    )
    active_positions = active_result.scalar() or 0

    # Profit factor
    gross_profit_result = await db.execute(
        select(func.sum(Trade.realized_pnl))
        .where(and_(
            Trade.bot_id == bot.id, Trade.status == TradeStatus.CLOSED,
            Trade.realized_pnl > 0,
        ))
    )
    gross_profit = gross_profit_result.scalar() or 0

    gross_loss_result = await db.execute(
        select(func.sum(Trade.realized_pnl))
        .where(and_(
            Trade.bot_id == bot.id, Trade.status == TradeStatus.CLOSED,
            Trade.realized_pnl < 0,
        ))
    )
    gross_loss = abs(gross_loss_result.scalar() or 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    return OverviewStats(
        balance=bot.peak_equity,
        today_pnl=bot.daily_pnl,
        monthly_pnl=monthly_pnl,
        total_pnl=total_pnl,
        win_rate=(winning / total_trades * 100) if total_trades > 0 else 0,
        total_trades=total_trades,
        sharpe_ratio=None,  # Computed from daily returns
        profit_factor=profit_factor,
        max_drawdown=0,
        active_positions=active_positions,
        bot_status=bot.status.value,
        trading_mode=settings.TRADING_MODE.value,
    )


@router.get("/daily", response_model=list[DailyPerformance])
async def get_daily_performance(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get daily performance breakdown."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return []

    result = await db.execute(
        select(PerformanceMetric)
        .where(PerformanceMetric.bot_id == bot_id)
        .order_by(desc(PerformanceMetric.date))
        .limit(days)
    )
    metrics = result.scalars().all()

    return [
        DailyPerformance(
            date=m.date.isoformat(),
            pnl=m.daily_pnl,
            pnl_pct=m.daily_pnl_pct,
            trades=m.total_trades,
            win_rate=m.win_rate,
            drawdown=m.max_drawdown,
        )
        for m in reversed(list(metrics))
    ]


@router.get("/equity", response_model=list[EquityPoint])
async def get_equity_curve(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get equity curve data points."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return []

    result = await db.execute(
        select(PerformanceMetric)
        .where(PerformanceMetric.bot_id == bot_id)
        .order_by(PerformanceMetric.date)
        .limit(days)
    )
    metrics = result.scalars().all()

    return [
        EquityPoint(date=m.date.isoformat(), equity=m.ending_equity)
        for m in metrics
    ]


@router.get("/strategies", response_model=list[StrategyPerformance])
async def get_strategy_analytics(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get performance breakdown by strategy."""
    result = await db.execute(select(Strategy).where(Strategy.is_active == True))
    strategies = result.scalars().all()

    return [
        StrategyPerformance(
            name=s.name,
            type=s.type.value,
            total_trades=s.total_trades,
            win_rate=s.win_rate,
            avg_pnl=s.avg_pnl,
            total_pnl=s.avg_pnl * s.total_trades,
        )
        for s in strategies
    ]
