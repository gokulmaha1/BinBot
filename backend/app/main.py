"""
BinBot AI Auto Mode — FastAPI Application Factory
Main entry point for the trading platform.
"""

import logging
import sys
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from app.config import settings
from app.db.session import init_db, close_db
from app.deps import close_redis

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)-25s │ %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("binbot")


# ── Lifespan ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("=" * 60)
    logger.info(f"  {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"  Mode: {settings.TRADING_MODE.value.upper()}")
    logger.info("=" * 60)

    # Startup
    await init_db()
    logger.info("Database initialized")

    # Seed default strategies
    await _seed_strategies()

    # Resume active bots
    await _resume_active_bots()

    yield

    # Shutdown
    await close_redis()
    await close_db()
    logger.info("Shutdown complete")


async def _seed_strategies():
    """Seed the strategies table with default strategy definitions."""
    from app.db.session import async_session_factory
    from app.models import Strategy, StrategyType
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(select(Strategy).limit(1))
        if result.scalar_one_or_none():
            return  # Already seeded

        defaults = [
            Strategy(name="Trend Following", type=StrategyType.TREND, parameters={
                "ema_fast": 9, "ema_mid": 21, "ema_slow": 50,
                "adx_min": 25, "rsi_range": [35, 65],
            }),
            Strategy(name="Momentum Breakout", type=StrategyType.BREAKOUT, parameters={
                "sr_lookback": 20, "volume_spike_ratio": 2.0,
                "macd_confirmation": True,
            }),
            Strategy(name="Mean Reversion", type=StrategyType.REVERSION, parameters={
                "bb_period": 20, "bb_std": 2.0,
                "rsi_oversold": 20, "rsi_overbought": 80,
            }),
            Strategy(name="Scalping", type=StrategyType.SCALP, parameters={
                "order_flow_threshold": 0.7,
                "micro_ema_period": 5,
            }),
            Strategy(name="Volatility Expansion", type=StrategyType.VOLATILITY, parameters={
                "atr_expansion_ratio": 1.5,
                "supertrend_period": 10, "supertrend_mult": 3.0,
            }),
        ]

        for s in defaults:
            session.add(s)
        await session.commit()
        logger.info("Default strategies seeded")


async def _resume_active_bots():
    """Resume any bots that were running or paused before the server restarted."""
    from app.db.session import async_session_factory
    from app.models import Bot, BotStatus
    from sqlalchemy import select

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Bot).where(Bot.status.in_([BotStatus.RUNNING, BotStatus.PAUSED]))
            )
            bots = result.scalars().all()
            for bot in bots:
                logger.info(f"Auto-resuming bot {bot.id} in state: {bot.status.value}")
                is_paused = bot.status == BotStatus.PAUSED
                await _bot_service.start(str(bot.id), paused=is_paused)
    except Exception as e:
        logger.error(f"Error resuming active bots: {e}", exc_info=True)


# ── Create App ───────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Socket.IO Integration ───────────────────────────────────────
from app.api.websocket import sio
sio_app = socketio.ASGIApp(sio, other_asgi_app=app)

# ── Register API Routers ────────────────────────────────────────
from app.api.auth import router as auth_router
from app.api.bot import router as bot_router, set_bot_service
from app.api.trades import router as trades_router
from app.api.config import router as config_router
from app.api.scanner import router as scanner_router
from app.api.signals import router as signals_router
from app.api.analytics import router as analytics_router
from app.api.logs import router as logs_router

app.include_router(auth_router)
app.include_router(bot_router)
app.include_router(trades_router)
app.include_router(config_router)
app.include_router(scanner_router)
app.include_router(signals_router)
app.include_router(analytics_router)
app.include_router(logs_router)

# ── Register Bot Service ────────────────────────────────────────
from app.services.bot_service import BotService
_bot_service = BotService()
set_bot_service(_bot_service)

# ── Static Files & Dashboard Routes ─────────────────────────────
import os
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend")

if os.path.isdir(FRONTEND_DIR):
    app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")


@app.get("/")
async def root():
    """Redirect to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
async def dashboard():
    """Serve main dashboard."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/dashboard/{page}")
async def dashboard_page(page: str):
    """Serve dashboard sub-pages."""
    filepath = os.path.join(FRONTEND_DIR, f"{page}")
    if not filepath.endswith(".html"):
        filepath += ".html"
    if os.path.isfile(filepath):
        return FileResponse(filepath)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/login")
async def login_page():
    """Serve login page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


# ── Health Check ─────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "mode": settings.TRADING_MODE.value,
    }
