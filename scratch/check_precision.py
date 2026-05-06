import config
from binance.client import Client

client = Client(config.API_KEY, config.API_SECRET)
info = client.futures_exchange_info()

for item in info['symbols']:
    if item['symbol'] == 'LABUSDT':
        print(f"Symbol: {item['symbol']}")
        print(f"Price Precision: {item['pricePrecision']}")
        print(f"Quantity Precision: {item['quantityPrecision']}")
        for f in item['filters']:
            if f['filterType'] == 'LOT_SIZE':
                print(f"Step Size: {f['stepSize']}")
            if f['filterType'] == 'PRICE_FILTER':
                print(f"Tick Size: {f['tickSize']}")
