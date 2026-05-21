import inspect
from binance.client import Client
print(inspect.signature(Client.futures_create_algo_order))
