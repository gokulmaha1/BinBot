import time
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from strategy import generate_signal
from risk import calculate_quantity, get_tp_sl_prices
from execution import place_order, place_tp_sl
from state import has_open_position
from logger import log

def get_data(client, symbol, interval):
    try:
        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            limit=100
        )
        df = pd.DataFrame(klines)
        df = df.iloc[:, :6]
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        df['close'] = df['close'].astype(float)
        return df
    except Exception as e:
        log(f"Error fetching data: {e}", "error")
        return None

def get_balance(client):
    try:
        balance = client.futures_account_balance()
        for b in balance:
            if b['asset'] == 'USDT':
                return float(b['balance'])
        return 0
    except Exception as e:
        log(f"Error fetching balance: {e}", "error")
        return 0

def run_bot():
    log("Initializing Binance Trading Bot...")
    
    try:
        client = Client(config.API_KEY, config.API_SECRET)
        
        # Initial check and setup
        client.futures_change_leverage(
            symbol=config.SYMBOL,
            leverage=config.LEVERAGE
        )
        log(f"Bot started for {config.SYMBOL} with {config.LEVERAGE}x leverage.")
        
    except Exception as e:
        log(f"Initialization failed: {e}", "error")
        return

    while True:
        try:
            # 1. Check for open positions
            log("Checking for open positions...", "info")
            if has_open_position(client, config.SYMBOL):
                log(f"Position already open for {config.SYMBOL}. Monitoring...", "info")
                time.sleep(60)
                continue

            # 2. Get Market Data
            log("Fetching market data...", "info")
            df = get_data(client, config.SYMBOL, config.TIMEFRAME)
            if df is None:
                log("Failed to fetch data, retrying in 30s...", "warning")
                time.sleep(30)
                continue

            # 3. Generate Signal
            log("Generating signal...", "info")
            signal = generate_signal(df)
            
            if signal:
                log(f"Signal Generated: {signal}", "info")
                
                # 4. Calculate Risk and Quantity
                balance = get_balance(client)
                price = df['close'].iloc[-1]
                
                if balance < 10: # Minimum capital check
                    log(f"Insufficient balance: ${balance}", "warning")
                    time.sleep(300)
                    continue
                    
                qty = calculate_quantity(balance, price)
                
                # 5. Execute Trade
                log(f"Executing {signal} order for {qty} {config.SYMBOL} at {price}", "info")
                order = place_order(client, config.SYMBOL, signal, qty)
                
                if order:
                    entry_price = float(order.get('avgPrice', price))
                    tp_price, sl_price = get_tp_sl_prices(signal, entry_price)
                    
                    # 6. Place TP/SL
                    success = place_tp_sl(client, config.SYMBOL, signal, tp_price, sl_price)
                    if success:
                        log(f"Trade Live | Side: {signal} | Entry: {entry_price} | TP: {tp_price} | SL: {sl_price}")
                    else:
                        log("Failed to place TP/SL. PLEASE MANAGE POSITION MANUALLY!", "error")
                else:
                    log("Order execution failed.", "error")

            # Wait for next check
            time.sleep(60)

        except BinanceAPIException as e:
            log(f"Binance API Error: {e.message}", "error")
            time.sleep(60)
        except Exception as e:
            log(f"Unexpected Error: {str(e)}", "error")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
