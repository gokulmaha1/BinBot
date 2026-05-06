import asyncio
import pandas as pd
from binance import BinanceSocketManager

class MarketStream:
    def __init__(self, client, symbol, interval):
        self.client = client
        self.symbol = symbol
        self.interval = interval
        self.latest_df = None
        self.bm = BinanceSocketManager(self.client)

    async def start(self):
        ts = self.bm.kline_socket(self.symbol, self.interval)
        async with ts as tscm:
            while True:
                res = await tscm.recv()
                if res and 'k' in res:
                    k = res['k']
                    # We only need to know when a candle closes or update our latest price
                    # But for simplicity, we can just trigger a re-fetch of full history 
                    # when we get a message to ensure all indicators are correct.
                    # Or we can update the last row.
                    pass

    def get_latest_data(self):
        return self.latest_df
