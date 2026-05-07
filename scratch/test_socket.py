import sys
import asyncio
from binance import AsyncClient, BinanceSocketManager
from bot import config

async def test_socket(symbol="btcusdt"):
    print(f"TEST: Probing {symbol.upper()} Socket...")
    client = await AsyncClient.create(config.API_KEY, config.API_SECRET, testnet=config.USE_TESTNET)
    bm = BinanceSocketManager(client)
    try:
        ts = bm.symbol_ticker_socket(symbol)
        async with ts as tscm:
            for i in range(5):
                res = await tscm.recv()
                print(f"SUCCESS: Connected to {symbol.upper()} stream.")
                if res and 'c' in res:
                    print(f"LIVE DATA: {symbol.upper()} Price: ${res['c']}")
            print(f"{symbol.upper()} OK.")
    finally:
        await client.close_connection()

if __name__ == "__main__":
    symbol = sys.argv[1].lower() if len(sys.argv) > 1 else "btcusdt"
    asyncio.run(test_socket(symbol))
