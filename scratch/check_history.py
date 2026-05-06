import os
from binance.client import Client
from bot import config
from dotenv import load_dotenv

def check_history():
    load_dotenv()
    client = Client(config.API_KEY, config.API_SECRET)
    
    print(f"--- Fetching History for {config.SYMBOL} ---")
    trades = client.futures_account_trades(symbol=config.SYMBOL, limit=20)
    
    for t in trades:
        print(f"Time: {t['time']}, Side: {t['side']}, Price: {t['price']}, Qty: {t['qty']}, PnL: {t['realizedPnl']}")

if __name__ == "__main__":
    check_history()
