"""
BinBot AI Auto Mode — Scanner API
Expose pair scanner results and market snapshots.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.deps import get_session, get_current_user, get_redis
from app.models import MarketSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scanner", tags=["Scanner"])


class PairRankResponse(BaseModel):
    symbol: str
    price: float
    volume_24h: float
    open_interest: float | None
    atr: float | None
    adx: float | None
    regime: str | None
    scanner_score: float
    captured_at: str


@router.get("/ranked", response_model=list[PairRankResponse])
async def get_ranked_pairs(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get the latest ranked pairs from the scanner."""
    # Try Redis cache first
    redis = await get_redis()
    cached = await redis.get("scanner:ranked_pairs")

    if cached:
        import json
        pairs = json.loads(cached)
        return [PairRankResponse(**p) for p in pairs[:limit]]

    # Fallback to DB — get latest snapshot per symbol
    result = await db.execute(
        select(MarketSnapshot)
        .order_by(desc(MarketSnapshot.scanner_score))
        .limit(limit)
    )
    snapshots = result.scalars().all()

    return [
        PairRankResponse(
            symbol=s.symbol,
            price=s.price,
            volume_24h=s.volume_24h,
            open_interest=s.open_interest or 0,
            atr=s.atr,
            adx=s.adx,
            regime=s.regime,
            scanner_score=s.scanner_score,
            captured_at=s.captured_at.isoformat(),
        )
        for s in snapshots
    ]


@router.get("/history/{symbol}", response_model=list[PairRankResponse])
async def get_pair_history(
    symbol: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get historical scanner snapshots for a specific symbol."""
    result = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol.upper())
        .order_by(desc(MarketSnapshot.captured_at))
        .limit(limit)
    )
    snapshots = result.scalars().all()

    return [
        PairRankResponse(
            symbol=s.symbol,
            price=s.price,
            volume_24h=s.volume_24h,
            open_interest=s.open_interest or 0,
            atr=s.atr,
            adx=s.adx,
            regime=s.regime,
            scanner_score=s.scanner_score,
            captured_at=s.captured_at.isoformat(),
        )
        for s in snapshots
    ]
