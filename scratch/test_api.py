import os
from binance.client import Client
from dotenv import load_dotenv
import sys

# Add the root directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import config

def test_binance_connectivity():
    print("--- BinBot PRO: API Connectivity Tester ---")
    
    # Load Keys
    api_key = config.API_KEY
    api_secret = config.API_SECRET
    
    if not api_key or api_key == "YOUR_API_KEY":
        print("❌ ERROR: API Key is missing or default. Check bot/config.py")
        return

    client = Client(api_key, api_secret)
    
    # 1. Test Ticker (Public Data - No Key Needed)
    try:
        ticker = client.futures_symbol_ticker(symbol="BTCUSDT")
        print(f"PASS: Public API: OK (BTC Price: {ticker['price']})")
    except Exception as e:
        print(f"FAIL: Public API: FAILED ({e})")

    # 2. Test Account Info (Needs Key + Correct IP)
    try:
        acc = client.futures_account()
        print(f"PASS: Private API (Key/IP): OK")
        print(f"Balance: Available Balance: ${acc['availableBalance']}")
    except Exception as e:
        print(f"FAIL: Private API (Key/IP): FAILED")
        print(f"   Reason: {e}")
        if "-2015" in str(e):
            print("\nACTION REQUIRED: Go to Binance API Management and:")
            print("   1. Add IP '106.192.77.94' to the whitelist.")
            print("   2. Ensure 'Enable Futures' permission is checked.")

    # 3. Test Symbol Precision (Needs Permissions)
    try:
        info = client.futures_exchange_info()
        print("PASS: Futures Permissions: OK")
    except Exception as e:
        print(f"FAIL: Futures Permissions: FAILED ({e})")

if __name__ == "__main__":
    test_binance_connectivity()
