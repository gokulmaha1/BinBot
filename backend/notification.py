import os
import asyncio
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

class NotificationService:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id and "your_telegram_token" not in self.token)
        if self.enabled:
            try:
                self.bot = Bot(token=self.token)
            except Exception as e:
                print(f"Telegram Bot Init Failed: {e}")
                self.enabled = False

    async def send_message(self, message):
        if self.enabled:
            try:
                await self.bot.send_message(self.chat_id, f"🤖 *BinBot Pro Alert*\n\n{message}", parse_mode="Markdown")
            except Exception as e:
                print(f"Telegram Error: {e}")

    async def notify_trade(self, side, symbol, price, qty):
        msg = f"🚀 *{side} Order Executed*\nSymbol: `{symbol}`\nPrice: `{price}`\nQty: `{qty}`"
        await self.send_message(msg)

    async def notify_exit(self, side, symbol, price, pnl):
        emoji = "💰" if pnl > 0 else "📉"
        msg = f"{emoji} *Position Closed*\nSymbol: `{symbol}`\nExit Price: `{price}`\nPnL: `${pnl:.2f}`"
        await self.send_message(msg)
