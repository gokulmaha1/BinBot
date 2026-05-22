"""
BinBot AI Auto Mode — Trades API
Trade CRUD, protection updates, and trade history.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_
from sqlalchemy.orm import selectinload

from app.deps import get_session, get_current_user
from app.models import Trade, TradeStatus, TradeState, Bot, SignalSide, Position
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trades", tags=["Trades"])


# ── Response Models ──────────────────────────────────────────────

class TradeResponse(BaseModel):
    id: str
    symbol: str
    side: str
    strategy_name: str
    leverage: int
    entry_price: float | None
    exit_price: float | None
    quantity: float
    remaining_quantity: float
    sl_price: float
    tp1_price: float | None
    tp2_price: float | None
    tp3_price: float | None
    realized_pnl: float
    unrealized_pnl: float = 0.0
    mark_price: float = 0.0
    fees: float
    slippage: float
    status: str
    trade_state: str
    close_reason: str | None
    entry_time: str | None
    exit_time: str | None

class TradeListResponse(BaseModel):
    trades: list[TradeResponse]
    total: int
    page: int
    page_size: int

class UpdateProtectionRequest(BaseModel):
    sl_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    tp3_price: Optional[float] = None

class TradeStatsResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    best_trade: float
    worst_trade: float
    avg_holding_time_minutes: float


# ── Helpers ──────────────────────────────────────────────────────

def _trade_to_response(trade: Trade) -> TradeResponse:
    pos = trade.position if hasattr(trade, 'position') and trade.position else None
    return TradeResponse(
        id=str(trade.id),
        symbol=trade.symbol,
        side=trade.side.value,
        strategy_name=trade.strategy_name,
        leverage=trade.leverage,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        quantity=trade.quantity,
        remaining_quantity=trade.remaining_quantity,
        sl_price=trade.sl_price,
        tp1_price=trade.tp1_price,
        tp2_price=trade.tp2_price,
        tp3_price=trade.tp3_price,
        realized_pnl=trade.realized_pnl,
        unrealized_pnl=pos.unrealized_pnl if pos else 0.0,
        mark_price=pos.mark_price if pos else 0.0,
        fees=trade.fees,
        slippage=trade.slippage,
        status=trade.status.value,
        trade_state=trade.trade_state.value,
        close_reason=trade.close_reason,
        entry_time=trade.entry_time.isoformat() if trade.entry_time else None,
        exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
    )


# ── Routes ───────────────────────────────────────────────────────

@router.get("/", response_model=TradeListResponse)
async def list_trades(
    status: Optional[str] = Query(None, description="Filter by status: pending|open|partial_tp|closed"),
    symbol: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """List trades with optional filters and pagination."""
    # Get bot for this user
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return TradeListResponse(trades=[], total=0, page=page, page_size=page_size)

    # Build query
    query = select(Trade).where(Trade.bot_id == bot_id)
    count_query = select(func.count(Trade.id)).where(Trade.bot_id == bot_id)

    if status:
        try:
            ts = TradeStatus(status)
            query = query.where(Trade.status == ts)
            count_query = count_query.where(Trade.status == ts)
        except ValueError:
            pass

    if symbol:
        query = query.where(Trade.symbol == symbol.upper())
        count_query = count_query.where(Trade.symbol == symbol.upper())

    # Count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Fetch page
    query = query.order_by(desc(Trade.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    trades = result.scalars().all()

    return TradeListResponse(
        trades=[_trade_to_response(t) for t in trades],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/active", response_model=list[TradeResponse])
async def get_active_trades(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get all currently open/partial trades."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return []

    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.position))
        .where(
            and_(
                Trade.bot_id == bot_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
            )
        )
        .order_by(desc(Trade.entry_time))
    )
    trades = result.scalars().all()
    return [_trade_to_response(t) for t in trades]


@router.get("/stats", response_model=TradeStatsResponse)
async def get_trade_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get trade statistics for the last N days."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return TradeStatsResponse(
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0, total_pnl=0, avg_pnl=0, best_trade=0,
            worst_trade=0, avg_holding_time_minutes=0,
        )

    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(Trade)
        .where(
            and_(
                Trade.bot_id == bot_id,
                Trade.status == TradeStatus.CLOSED,
                Trade.exit_time >= cutoff,
            )
        )
    )
    trades = result.scalars().all()

    if not trades:
        return TradeStatsResponse(
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0, total_pnl=0, avg_pnl=0, best_trade=0,
            worst_trade=0, avg_holding_time_minutes=0,
        )

    pnls = [t.realized_pnl for t in trades]
    winning = [p for p in pnls if p > 0]
    losing = [p for p in pnls if p <= 0]

    # Average holding time
    holding_times = []
    for t in trades:
        if t.entry_time and t.exit_time:
            delta = (t.exit_time - t.entry_time).total_seconds() / 60
            holding_times.append(delta)

    return TradeStatsResponse(
        total_trades=len(trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=len(winning) / len(trades) * 100 if trades else 0,
        total_pnl=sum(pnls),
        avg_pnl=sum(pnls) / len(pnls) if pnls else 0,
        best_trade=max(pnls) if pnls else 0,
        worst_trade=min(pnls) if pnls else 0,
        avg_holding_time_minutes=sum(holding_times) / len(holding_times) if holding_times else 0,
    )


@router.put("/{trade_id}/protection")
async def update_protection(
    trade_id: str,
    req: UpdateProtectionRequest,
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Manually update TP/SL levels for an open trade."""
    from uuid import UUID
    result = await db.execute(select(Trade).where(Trade.id == UUID(trade_id)))
    trade = result.scalar_one_or_none()

    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status not in (TradeStatus.OPEN, TradeStatus.PARTIAL_TP):
        raise HTTPException(status_code=400, detail="Trade is not open")

    if req.sl_price is not None:
        trade.sl_price = req.sl_price
    if req.tp1_price is not None:
        trade.tp1_price = req.tp1_price
    if req.tp2_price is not None:
        trade.tp2_price = req.tp2_price
    if req.tp3_price is not None:
        trade.tp3_price = req.tp3_price

    await db.flush()
    logger.info(f"Protection updated for trade {trade_id}")

    return {"success": True, "message": "Protection levels updated"}


@router.post("/sync")
async def sync_positions(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually sync DB trade records with real Binance positions.
    - Closes DB trades whose symbols no longer have an open position on Binance.
    - Creates DB records for positions on Binance not tracked in DB.
    """
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return {"synced": 0, "closed": 0, "created": 0, "errors": ["No bot found"]}

    # ── Fetch open DB trades ──────────────────────────────────
    result = await db.execute(
        select(Trade).where(
            and_(
                Trade.bot_id == bot_id,
                Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIAL_TP]),
            )
        )
    )
    db_trades: list[Trade] = list(result.scalars().all())
    db_symbols = {t.symbol: t for t in db_trades}

    # ── Fetch Binance positions ───────────────────────────────
    from binance import AsyncClient
    binance_client = None
    try:
        if settings.is_testnet:
            binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
                testnet=True,
            )
        else:
            binance_client = await AsyncClient.create(
                api_key=settings.active_api_key,
                api_secret=settings.active_api_secret,
            )
        account = await binance_client.futures_account()
        binance_positions = account.get("positions", [])
    except Exception as exc:
        return {"synced": 0, "closed": 0, "created": 0, "errors": [str(exc)]}
    finally:
        if binance_client:
            await binance_client.close_connection()

    # Positions with non-zero amount
    live_symbols = {}
    for p in binance_positions:
        amt = float(p.get("positionAmt", 0))
        if abs(amt) >= 0.0001:
            live_symbols[p["symbol"]] = {
                "quantity": abs(amt),
                "side": SignalSide.BUY if amt > 0 else SignalSide.SELL,
                "entry_price": float(p.get("entryPrice", 0)),
                "mark_price": float(p.get("markPrice", 0)),
                "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
            }

    closed = 0
    created = 0

    # ── Close DB trades no longer on Binance ───────────────────
    for trade in db_trades:
        if trade.symbol not in live_symbols:
            exit_price = trade.entry_price
            trade.status = TradeStatus.CLOSED
            trade.exit_price = exit_price
            trade.exit_time = datetime.utcnow()
            trade.realized_pnl = 0.0
            trade.remaining_quantity = 0.0
            trade.close_reason = "manual_sync"
            if trade.trade_state == TradeState.ENTRY:
                trade.trade_state = TradeState.SL_HIT
            closed += 1
            logger.info("Manual sync: closed %s (%s)", trade.symbol, trade.id)

    # ── Create DB records for orphaned Binance positions ──────
    for symbol, pos in live_symbols.items():
        if symbol not in db_symbols:
            import uuid as _uuid
            now = datetime.utcnow()
            trade = Trade(
                id=_uuid.uuid4(),
                bot_id=bot_id,
                signal_id=None,
                symbol=symbol,
                side=pos["side"],
                strategy_name="manual_sync",
                leverage=1,
                entry_price=pos["entry_price"],
                quantity=pos["quantity"],
                remaining_quantity=pos["quantity"],
                status=TradeStatus.OPEN,
                trade_state=TradeState.ENTRY,
                entry_time=now,
                close_reason=None,
            )
            db.add(trade)

            from app.models import Position
            position = Position(
                id=_uuid.uuid4(),
                trade_id=trade.id,
                symbol=symbol,
                side=pos["side"],
                quantity=pos["quantity"],
                entry_price=pos["entry_price"],
                mark_price=pos["mark_price"],
                unrealized_pnl=pos["unrealized_pnl"],
                timestamp=now,
            )
            db.add(position)
            created += 1
            logger.info("Manual sync: created record for %s", symbol)

    await db.commit()
    return {
        "synced": len(db_trades),
        "closed": closed,
        "created": created,
        "live_positions": len(live_symbols),
    }
