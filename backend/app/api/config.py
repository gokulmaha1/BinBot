"""
BinBot AI Auto Mode — Config API
Bot settings CRUD and exchange account management.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, TradingMode
from app.deps import get_session, get_current_user, get_redis
from app.models import Bot, ExchangeAccount, ExchangeMode
from app.services.crypto import encrypt, decrypt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["Configuration"])


# ── Request/Response Models ──────────────────────────────────────

class BotConfigResponse(BaseModel):
    # Risk limits (read-only, hardcoded)
    max_risk_per_trade: float
    max_daily_loss: float
    max_drawdown: float
    max_leverage: int
    max_active_positions: int
    max_trades_per_day: int
    signal_score_threshold: int
    ml_confidence_threshold: float

    # Configurable
    capital_per_trade_pct: float
    trading_mode: str

    # TP tiers
    tp1_ratio: float
    tp1_close_pct: float
    tp2_ratio: float
    tp2_close_pct: float
    tp3_ratio: float
    tp3_close_pct: float

    # Scanner
    scanner_min_volume_24h: float
    scanner_top_pairs: int
    scanner_manual_pairs: str

    # Indicators
    ema_fast: int
    ema_mid: int
    ema_slow: int
    rsi_period: int
    atr_period: int


class UpdateConfigRequest(BaseModel):
    capital_per_trade_pct: Optional[float] = None
    trading_mode: Optional[str] = None
    tp1_ratio: Optional[float] = None
    tp1_close_pct: Optional[float] = None
    tp2_ratio: Optional[float] = None
    tp2_close_pct: Optional[float] = None
    tp3_ratio: Optional[float] = None
    tp3_close_pct: Optional[float] = None
    scanner_min_volume_24h: Optional[float] = None
    scanner_top_pairs: Optional[int] = None
    scanner_manual_pairs: Optional[str] = None


class ExchangeAccountRequest(BaseModel):
    api_key: str
    api_secret: str
    mode: str = "testnet"  # paper | testnet | live


class ExchangeAccountResponse(BaseModel):
    id: str
    exchange: str
    mode: str
    is_active: bool
    api_key_preview: str  # Only show last 4 chars


# ── Routes ───────────────────────────────────────────────────────

@router.get("/", response_model=BotConfigResponse)
async def get_config(current_user: dict = Depends(get_current_user)):
    """Get current bot configuration."""
    return BotConfigResponse(
        max_risk_per_trade=settings.MAX_RISK_PER_TRADE,
        max_daily_loss=settings.MAX_DAILY_LOSS,
        max_drawdown=settings.MAX_DRAWDOWN,
        max_leverage=settings.MAX_LEVERAGE,
        max_active_positions=settings.MAX_ACTIVE_POSITIONS,
        max_trades_per_day=settings.MAX_TRADES_PER_DAY,
        signal_score_threshold=settings.SIGNAL_SCORE_THRESHOLD,
        ml_confidence_threshold=settings.ML_CONFIDENCE_THRESHOLD,
        capital_per_trade_pct=settings.CAPITAL_PER_TRADE_PCT,
        trading_mode=settings.TRADING_MODE.value,
        tp1_ratio=settings.TP1_RATIO,
        tp1_close_pct=settings.TP1_CLOSE_PCT,
        tp2_ratio=settings.TP2_RATIO,
        tp2_close_pct=settings.TP2_CLOSE_PCT,
        tp3_ratio=settings.TP3_RATIO,
        tp3_close_pct=settings.TP3_CLOSE_PCT,
        scanner_min_volume_24h=settings.SCANNER_MIN_VOLUME_24H,
        scanner_top_pairs=settings.SCANNER_TOP_PAIRS,
        scanner_manual_pairs=settings.SCANNER_MANUAL_PAIRS,
        ema_fast=settings.EMA_FAST,
        ema_mid=settings.EMA_MID,
        ema_slow=settings.EMA_SLOW,
        rsi_period=settings.RSI_PERIOD,
        atr_period=settings.ATR_PERIOD,
    )


@router.put("/")
async def update_config(
    req: UpdateConfigRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update configurable bot settings. Risk limits are NOT modifiable."""
    updates = {}
    if req.capital_per_trade_pct is not None:
        if 0.01 <= req.capital_per_trade_pct <= 0.50:
            settings.CAPITAL_PER_TRADE_PCT = req.capital_per_trade_pct
            updates["capital_per_trade_pct"] = req.capital_per_trade_pct
        else:
            raise HTTPException(400, "capital_per_trade_pct must be between 0.01 and 0.50")

    if req.scanner_min_volume_24h is not None:
        settings.SCANNER_MIN_VOLUME_24H = req.scanner_min_volume_24h
        updates["scanner_min_volume_24h"] = req.scanner_min_volume_24h

    if req.scanner_top_pairs is not None:
        settings.SCANNER_TOP_PAIRS = max(5, min(50, req.scanner_top_pairs))
        updates["scanner_top_pairs"] = settings.SCANNER_TOP_PAIRS

    if req.scanner_manual_pairs is not None:
        cleaned = ",".join([s.strip().upper() for s in req.scanner_manual_pairs.split(",") if s.strip()])
        settings.SCANNER_MANUAL_PAIRS = cleaned
        updates["scanner_manual_pairs"] = cleaned

    if req.trading_mode is not None:
        try:
            trading_mode_enum = TradingMode(req.trading_mode)
            settings.TRADING_MODE = trading_mode_enum
            updates["trading_mode"] = trading_mode_enum.value
        except ValueError:
            raise HTTPException(400, f"Invalid trading mode: {req.trading_mode}")

    # TP tiers
    for field in ["tp1_ratio", "tp1_close_pct", "tp2_ratio", "tp2_close_pct", "tp3_ratio", "tp3_close_pct"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(settings, field.upper(), val)
            updates[field] = val

    logger.info(f"Config updated: {updates}")

    # Persist configurable settings to Redis so they survive restarts
    try:
        redis = await get_redis()
        config_snapshot = {
            "capital_per_trade_pct": settings.CAPITAL_PER_TRADE_PCT,
            "trading_mode": settings.TRADING_MODE.value,
            "tp1_ratio": settings.TP1_RATIO,
            "tp1_close_pct": settings.TP1_CLOSE_PCT,
            "tp2_ratio": settings.TP2_RATIO,
            "tp2_close_pct": settings.TP2_CLOSE_PCT,
            "tp3_ratio": settings.TP3_RATIO,
            "tp3_close_pct": settings.TP3_CLOSE_PCT,
            "scanner_min_volume_24h": settings.SCANNER_MIN_VOLUME_24H,
            "scanner_top_pairs": settings.SCANNER_TOP_PAIRS,
            "scanner_manual_pairs": settings.SCANNER_MANUAL_PAIRS,
        }
        await redis.set("binbot:config", json.dumps(config_snapshot))
        logger.info("Config persisted to Redis")
    except Exception as exc:
        logger.warning(f"Failed to persist config to Redis: {exc}")

    return {"success": True, "updated": updates}


@router.post("/exchange", response_model=ExchangeAccountResponse)
async def add_exchange_account(
    req: ExchangeAccountRequest,
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Add an encrypted exchange API key pair."""
    from uuid import UUID

    try:
        mode = ExchangeMode(req.mode)
    except ValueError:
        raise HTTPException(400, f"Invalid mode: {req.mode}. Use: paper, testnet, live")

    # Live mode requires explicit confirmation
    if mode == ExchangeMode.LIVE:
        logger.warning(f"User {current_user['user_id']} adding LIVE exchange account")

    account = ExchangeAccount(
        user_id=UUID(current_user["user_id"]),
        api_key_encrypted=encrypt(req.api_key),
        api_secret_encrypted=encrypt(req.api_secret),
        mode=mode,
    )
    db.add(account)
    await db.flush()

    return ExchangeAccountResponse(
        id=str(account.id),
        exchange=account.exchange,
        mode=account.mode.value,
        is_active=account.is_active,
        api_key_preview=f"****{req.api_key[-4:]}",
    )


@router.get("/exchange", response_model=list[ExchangeAccountResponse])
async def list_exchange_accounts(
    db: AsyncSession = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """List exchange accounts (keys are masked)."""
    from uuid import UUID
    result = await db.execute(
        select(ExchangeAccount)
        .where(ExchangeAccount.user_id == UUID(current_user["user_id"]))
    )
    accounts = result.scalars().all()

    responses = []
    for acc in accounts:
        try:
            key = decrypt(acc.api_key_encrypted)
            preview = f"****{key[-4:]}"
        except Exception:
            preview = "****"

        responses.append(ExchangeAccountResponse(
            id=str(acc.id),
            exchange=acc.exchange,
            mode=acc.mode.value,
            is_active=acc.is_active,
            api_key_preview=preview,
        ))

    return responses
