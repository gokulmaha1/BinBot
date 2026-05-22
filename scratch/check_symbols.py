import asyncio
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.config import settings
from binance import AsyncClient

async def main():
    c = await AsyncClient.create(api_key=settings.active_api_key, api_secret=settings.active_api_secret)
    info = await c.futures_exchange_info()
    syms = {s['symbol'] for s in info['symbols'] if s.get('contractType')=='PERPETUAL' and s.get('quoteAsset')=='USDT'}
    targets = ['AGTUSDT','ALTUSDT','AKEUSDT']
    for t in targets:
        status = 'EXISTS' if t in syms else 'NOT FOUND'
        print(f"{t}: {status}")
    await c.close_connection()

asyncio.run(main())
