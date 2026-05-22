"""
BinBot AI Auto Mode — Notification Service
Sends trade alerts via Telegram.
"""

import logging
from typing import Optional
from aiogram import Bot

from app.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends alerts to Telegram when trades are executed or closed."""

    def __init__(self):
        self.enabled = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)
        self.bot: Optional[Bot] = None
        if self.enabled:
            self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            logger.info("Telegram notifications enabled")
        else:
            logger.info("Telegram notifications disabled (no token/chat_id)")

    async def send_message(self, message: str) -> bool:
        """Send a raw message to Telegram."""
        if not self.enabled or not self.bot:
            return False
        try:
            await self.bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode="Markdown",
            )
            return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def notify_trade_entry(
        self,
        symbol: str,
        side: str,
        strategy: str,
        entry_price: float,
        quantity: float,
        leverage: int,
        sl_price: float,
        tp1_price: float,
        score: float,
        ml_confidence: float,
    ) -> bool:
        """Send trade entry alert."""
        msg = (
            f"🚀 *TRADE OPENED*\n\n"
            f"📊 *{symbol}* | {side}\n"
            f"🎯 Strategy: `{strategy}`\n"
            f"💰 Entry: `{entry_price}`\n"
            f"📦 Qty: `{quantity}` | Leverage: `{leverage}x`\n"
            f"🛡 SL: `{sl_price}`\n"
            f"🎯 TP1: `{tp1_price}`\n"
            f"📈 Signal Score: `{score}/100`\n"
            f"🤖 ML Confidence: `{ml_confidence:.1%}`\n"
            f"⏰ Mode: `{settings.TRADING_MODE.value.upper()}`"
        )
        return await self.send_message(msg)

    async def notify_trade_exit(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        close_reason: str,
    ) -> bool:
        """Send trade exit alert."""
        emoji = "✅" if pnl >= 0 else "❌"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        msg = (
            f"{emoji} *TRADE CLOSED*\n\n"
            f"📊 *{symbol}* | {side}\n"
            f"💰 Entry: `{entry_price}` → Exit: `{exit_price}`\n"
            f"💵 PnL: `{pnl_str}`\n"
            f"📝 Reason: `{close_reason}`"
        )
        return await self.send_message(msg)

    async def notify_risk_alert(self, alert_type: str, details: str) -> bool:
        """Send risk management alert."""
        msg = (
            f"⚠️ *RISK ALERT*\n\n"
            f"🚨 Type: `{alert_type}`\n"
            f"📝 {details}"
        )
        return await self.send_message(msg)

    async def notify_bot_status(self, status: str, reason: str = "") -> bool:
        """Send bot status change alert."""
        emoji = "🟢" if status == "running" else "🔴"
        msg = f"{emoji} *Bot {status.upper()}*"
        if reason:
            msg += f"\n📝 {reason}"
        return await self.send_message(msg)
