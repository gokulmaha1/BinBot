import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('TESTNET_API_KEY')
api_secret = os.getenv('TESTNET_API_SECRET')

client = Client(api_key, api_secret, testnet=True)

# List all methods of the client that look like they are for futures and algo
futures_methods = [m for m in dir(client) if 'futures' in m.lower() and 'algo' in m.lower()]
print("Found methods:", futures_methods)
