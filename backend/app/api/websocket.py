"""
BinBot AI Auto Mode — WebSocket API
Socket.IO handlers for real-time dashboard updates.
"""

import logging
import json

import socketio
from app.config import settings

logger = logging.getLogger(__name__)

# Create Socket.IO server (async mode)
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

# Track connected clients
connected_clients: set[str] = set()


@sio.event
async def connect(sid, environ):
    """Handle client connection."""
    connected_clients.add(sid)
    logger.info(f"Dashboard client connected: {sid} (total: {len(connected_clients)})")
    await sio.emit("connected", {"status": "ok", "mode": settings.TRADING_MODE.value}, room=sid)


@sio.event
async def disconnect(sid):
    """Handle client disconnection."""
    connected_clients.discard(sid)
    logger.info(f"Dashboard client disconnected: {sid} (total: {len(connected_clients)})")


@sio.event
async def subscribe_prices(sid, data):
    """Subscribe to price updates for specific symbols."""
    symbols = data.get("symbols", [])
    for symbol in symbols:
        await sio.enter_room(sid, f"price:{symbol}")
    logger.debug(f"Client {sid} subscribed to prices: {symbols}")


@sio.event
async def unsubscribe_prices(sid, data):
    """Unsubscribe from price updates."""
    symbols = data.get("symbols", [])
    for symbol in symbols:
        await sio.leave_room(sid, f"price:{symbol}")


# ── Broadcasting Functions (called by engine modules) ────────────

async def broadcast_price(symbol: str, price: float, change_24h: float = 0):
    """Broadcast price update to subscribed clients."""
    if connected_clients:
        await sio.emit(
            "price_update",
            {"symbol": symbol, "price": price, "change_24h": change_24h},
            room=f"price:{symbol}",
        )


async def broadcast_trade(trade_data: dict):
    """Broadcast trade event to all connected clients."""
    if connected_clients:
        await sio.emit("trade_update", trade_data)


async def broadcast_signal(signal_data: dict):
    """Broadcast new signal to all connected clients."""
    if connected_clients:
        await sio.emit("signal_update", signal_data)


async def broadcast_scanner(ranked_pairs: list[dict]):
    """Broadcast scanner results to all connected clients."""
    if connected_clients:
        await sio.emit("scanner_update", {"pairs": ranked_pairs})


async def broadcast_bot_status(status: str, details: dict = None):
    """Broadcast bot status change."""
    if connected_clients:
        await sio.emit("bot_status", {"status": status, **(details or {})})


async def broadcast_risk_alert(alert_type: str, message: str):
    """Broadcast risk management alert."""
    if connected_clients:
        await sio.emit("risk_alert", {"type": alert_type, "message": message})


async def broadcast_log(level: str, source: str, message: str):
    """Broadcast log entry to dashboard."""
    if connected_clients:
        await sio.emit("log", {"level": level, "source": source, "message": message})
