import asyncio
from binance import AsyncClient, BinanceSocketManager
import sys

async def main():
    print("TEST: Probing LABUSDT Socket...")
    try:
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        ts = bm.symbol_ticker_socket('labusdt') 
        
        async with ts as tscm:
            print("SUCCESS: Connected to LABUSDT stream.")
            res = await asyncio.wait_for(tscm.recv(), timeout=10)
            print(f"LIVE DATA: LABUSDT Price: ${res['c']}")
            
        await client.close_connection()
        print("LABUSDT OK.")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(main())
