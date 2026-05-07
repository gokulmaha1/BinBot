import sys
from binance.client import Client
from bot import config

# Usage: python check_precision.py BTCUSDT SOLUSDT ...
symbols_to_check = sys.argv[1:] if len(sys.argv) > 1 else ["BTCUSDT"]

client = Client(config.API_KEY, config.API_SECRET, testnet=config.USE_TESTNET)
info = client.futures_exchange_info()

for target in symbols_to_check:
    target = target.upper()
    found = False
    for item in info['symbols']:
        if item['symbol'] == target:
            found = True
            print(f"\n--- {item['symbol']} ---")
            print(f"Price Precision: {item['pricePrecision']}")
            print(f"Quantity Precision: {item['quantityPrecision']}")
            for f in item['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    print(f"Step Size: {f['stepSize']}")
                if f['filterType'] == 'PRICE_FILTER':
                    print(f"Tick Size: {f['tickSize']}")
    if not found:
        print(f"{target} not found in Futures Exchange Info.")
