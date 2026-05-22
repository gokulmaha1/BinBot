"""
BinBot AI Auto Mode — Signals API
Signal rankings, history, and score breakdowns.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_

from app.deps import get_session, get_current_user
from app.models import Signal, SignalStatus, Bot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/signals", tags=["Signals"])


class SignalResponse(BaseModel):
    id: str
    symbol: str
    side: str
    strategy_name: str
    score: float
    ml_confidence: float
    score_breakdown: dict
    regime: str | None
    status: str
    reject_reason: str | None
    created_at: str


class SignalListResponse(BaseModel):
    signals: list[SignalResponse]
    total: int
    page: int
    page_size: int


@router.get("/", response_model=SignalListResponse)
async def list_signals(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """List signals with optional filters."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return SignalListResponse(signals=[], total=0, page=page, page_size=page_size)

    query = select(Signal).where(Signal.bot_id == bot_id)
    count_query = select(func.count(Signal.id)).where(Signal.bot_id == bot_id)

    if status:
        try:
            ss = SignalStatus(status)
            query = query.where(Signal.status == ss)
            count_query = count_query.where(Signal.status == ss)
        except ValueError:
            pass

    if symbol:
        query = query.where(Signal.symbol == symbol.upper())
        count_query = count_query.where(Signal.symbol == symbol.upper())

    if min_score is not None:
        query = query.where(Signal.score >= min_score)
        count_query = count_query.where(Signal.score >= min_score)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(desc(Signal.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    signals = result.scalars().all()

    return SignalListResponse(
        signals=[
            SignalResponse(
                id=str(s.id),
                symbol=s.symbol,
                side=s.side.value,
                strategy_name=s.strategy_name,
                score=s.score,
                ml_confidence=s.ml_confidence,
                score_breakdown=s.score_breakdown,
                regime=s.regime,
                status=s.status.value,
                reject_reason=s.reject_reason,
                created_at=s.created_at.isoformat(),
            )
            for s in signals
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/top", response_model=list[SignalResponse])
async def get_top_signals(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get the highest-scored recent signals."""
    bot_result = await db.execute(
        select(Bot.id).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot_id = bot_result.scalar_one_or_none()
    if not bot_id:
        return []

    result = await db.execute(
        select(Signal)
        .where(Signal.bot_id == bot_id)
        .order_by(desc(Signal.score))
        .limit(limit)
    )
    signals = result.scalars().all()

    return [
        SignalResponse(
            id=str(s.id),
            symbol=s.symbol,
            side=s.side.value,
            strategy_name=s.strategy_name,
            score=s.score,
            ml_confidence=s.ml_confidence,
            score_breakdown=s.score_breakdown,
            regime=s.regime,
            status=s.status.value,
            reject_reason=s.reject_reason,
            created_at=s.created_at.isoformat(),
        )
        for s in signals
    ]
