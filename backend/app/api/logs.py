"""
BinBot AI Auto Mode — Logs API
Expose system status logs.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.deps import get_session, get_current_user
from app.models import Log, LogLevel, LogSource

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["Logs"])


class LogResponse(BaseModel):
    id: int
    bot_id: str | None
    level: str
    source: str
    message: str
    timestamp: str


@router.get("/", response_model=list[LogResponse])
async def list_logs(
    limit: int = Query(200, ge=1, le=500),
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Retrieve system and bot logs."""
    query = select(Log)

    if level:
        try:
            query = query.where(Log.level == LogLevel(level))
        except ValueError:
            pass

    if source:
        try:
            query = query.where(Log.source == LogSource(source))
        except ValueError:
            pass

    query = query.order_by(desc(Log.created_at)).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return [
        LogResponse(
            id=l.id,
            bot_id=str(l.bot_id) if l.bot_id else None,
            level=l.level.value,
            source=l.source.value,
            message=l.message,
            timestamp=l.created_at.isoformat(),
        )
        for l in logs
    ]
