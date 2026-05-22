"""
BinBot AI Auto Mode — Bot Control API
Start, stop, and query bot status.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.deps import get_session, get_current_user
from app.models import Bot, BotStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot", tags=["Bot Control"])

# Global reference to the running bot service (set by main.py on startup)
_bot_service = None


def set_bot_service(service):
    """Register the bot service instance (called from main.py)."""
    global _bot_service
    _bot_service = service


# ── Response Models ──────────────────────────────────────────────

class BotStatusResponse(BaseModel):
    status: str
    is_running: bool
    started_at: str | None = None
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0
    active_positions: int = 0
    trading_mode: str = "paper"


class BotActionResponse(BaseModel):
    success: bool
    message: str


# ── Routes ───────────────────────────────────────────────────────

@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Get current bot status."""
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = result.scalar_one_or_none()

    if not bot:
        return BotStatusResponse(status="idle", is_running=False)

    from app.config import settings
    return BotStatusResponse(
        status=bot.status.value,
        is_running=bot.status == BotStatus.RUNNING,
        started_at=bot.started_at.isoformat() if bot.started_at else None,
        daily_pnl=bot.daily_pnl,
        consecutive_losses=bot.consecutive_losses,
        trades_today=bot.trades_today,
        trading_mode=settings.TRADING_MODE.value,
    )


@router.post("/start", response_model=BotActionResponse)
async def start_bot(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Start the AI Auto Mode bot."""
    if _bot_service is None:
        raise HTTPException(status_code=500, detail="Bot service not initialized")

    # Get or create bot record
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = result.scalar_one_or_none()

    if not bot:
        bot = Bot(user_id=current_user["user_id"], name="AI Auto Bot")
        db.add(bot)
        await db.flush()

    if bot.status == BotStatus.RUNNING:
        if _bot_service and not _bot_service._running:
            logger.info("Bot status is RUNNING in DB but service is not running. Restarting service.")
        else:
            return BotActionResponse(success=False, message="Bot is already running")

    bot.status = BotStatus.RUNNING
    bot.started_at = datetime.utcnow()
    await db.flush()

    # Start the bot loop
    try:
        await _bot_service.start(str(bot.id))
    except Exception as e:
        logger.error(f"Failed to start bot service: {e}", exc_info=True)
        bot.status = BotStatus.ERROR
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to start bot service: {str(e)}")

    logger.info(f"Bot started for user {current_user['user_id']}")
    return BotActionResponse(success=True, message="AI Auto Mode started")


@router.post("/stop", response_model=BotActionResponse)
async def stop_bot(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Stop the bot."""
    if _bot_service is None:
        raise HTTPException(status_code=500, detail="Bot service not initialized")

    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = result.scalar_one_or_none()

    if not bot or bot.status != BotStatus.RUNNING:
        return BotActionResponse(success=False, message="Bot is not running")

    bot.status = BotStatus.IDLE
    bot.stopped_at = datetime.utcnow()
    await db.flush()

    await _bot_service.stop()
    logger.info(f"Bot stopped for user {current_user['user_id']}")

    return BotActionResponse(success=True, message="Bot stopped")


@router.post("/pause", response_model=BotActionResponse)
async def pause_bot(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Pause the bot (stop new trades, keep monitoring positions)."""
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = result.scalar_one_or_none()

    if not bot or bot.status != BotStatus.RUNNING:
        return BotActionResponse(success=False, message="Bot is not running")

    bot.status = BotStatus.PAUSED
    await db.flush()

    if _bot_service:
        await _bot_service.pause()

    return BotActionResponse(success=True, message="Bot paused — monitoring only")


@router.post("/reset_daily", response_model=BotActionResponse)
async def reset_daily_stats(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Reset daily PnL and consecutive loss counter."""
    result = await db.execute(
        select(Bot).where(Bot.user_id == current_user["user_id"]).limit(1)
    )
    bot = result.scalar_one_or_none()

    if not bot:
        return BotActionResponse(success=False, message="No bot found")

    bot.daily_pnl = 0.0
    bot.consecutive_losses = 0
    bot.trades_today = 0
    bot.cooldown_until = None
    await db.flush()

    return BotActionResponse(success=True, message="Daily stats reset")
