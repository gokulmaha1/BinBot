import sys
from binance.client import Client
from bot import config

def analyze_market(symbol="BTCUSDT"):
    """Analyze market data for any given symbol."""
    client = Client(config.API_KEY, config.API_SECRET, testnet=config.USE_TESTNET)
    klines = client.futures_klines(symbol=symbol, interval='1m', limit=100)
    prices = [float(k[4]) for k in klines]
    
    print(f"\n--- {symbol} Market Analysis ---")
    print(f"Current Price: ${prices[-1]:.4f}")
    print(f"1h High: ${max(prices[-60:]):.4f}")
    print(f"1h Low: ${min(prices[-60:]):.4f}")
    print(f"Volatility: {((max(prices[-60:]) - min(prices[-60:])) / min(prices[-60:])) * 100:.2f}%")

if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    analyze_market(symbol)
