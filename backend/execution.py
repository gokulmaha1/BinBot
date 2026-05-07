from binance.client import Client
from binance.enums import *
import os
import logging
from bot import config

class ExecutionEngine:
    def __init__(self, client):
        self.client = client

    def check_connections(self):
        """Pre-flight check: Validates API keys, permissions, and connectivity."""
        try:
            # 1. Test REST API + Permissions
            acc = self.client.futures_account()
            if not acc.get('canTrade'):
                return False, "API Key does not have TRADING permissions."
            
            # 2. Test Balance Access
            balances = self.client.futures_account_balance()
            usdt = next((b for b in balances if b['asset'] == 'USDT'), None)
            if not usdt:
                return False, "Could not find USDT balance in Futures wallet."
            
            return True, f"Connected. Balance: ${float(usdt['balance']):.2f} USDT"
        except Exception as e:
            err_str = str(e)
            if "Invalid API-key" in err_str:
                return False, "Invalid API Key or Secret."
            if "IP" in err_str:
                return False, "IP Not Whitelisted in Binance API settings."
            return False, f"Connection Error: {err_str}"

    def setup_account(self, symbol, leverage):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self.client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
        except Exception:
            pass

    def get_quantity_precision(self, symbol):
        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    for f in s['filters']:
                        if f['filterType'] == 'LOT_SIZE':
                            step_size = float(f['stepSize'])
                            precision = 0
                            if step_size < 1:
                                precision = len(str(step_size).split('.')[-1].rstrip('0'))
                            return precision
            return 0
        except:
            return 0

    def place_market_order(self, symbol, side, quantity):
        try:
            print(f"[EXECUTION] Placing MARKET {side} for {quantity} {symbol}")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            return order, None
        except Exception as e:
            print(f"[EXECUTION] Error: {e}")
            return None, str(e)

    def get_price_info(self, symbol):
        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    for f in s['filters']:
                        if f['filterType'] == 'PRICE_FILTER':
                            tick_size = float(f['tickSize'])
                            precision = len(str(f['tickSize']).split('.')[-1].rstrip('0')) if '.' in str(f['tickSize']) else 0
                            return tick_size, precision
            return 0.01, 2
        except:
            return 0.01, 2

    def round_price(self, symbol, price):
        tick_size, precision = self.get_price_info(symbol)
        if tick_size == 0: return round(price, 8)
        # Use Decimal-like precision logic for tick_size alignment
        rounded = round(round(float(price) / tick_size) * tick_size, precision)
        # Final safety: Ensure we don't have too many decimals for the exchange
        return float(f"{rounded:.{precision}f}")

    def round_quantity(self, symbol, qty):
        precision = self.get_quantity_precision(symbol)
        if precision == 0: return int(qty)
        return round(float(qty), precision)

    def place_limit_order(self, symbol, side, qty, price):
        try:
            rounded_price = self.round_price(symbol, price)
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=qty,
                price=rounded_price
            )
            return order, None
        except Exception as e:
            return None, str(e)

    def set_tp_sl(self, symbol, side, entry_price, tp_pct, sl_pct, absolute_sl=None):
        exit_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        
        # Calculate Prices
        if side == SIDE_BUY:
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct) if absolute_sl is None else absolute_sl
        else:
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct) if absolute_sl is None else absolute_sl
            
        tp_price = self.round_price(symbol, tp_price)
        sl_price = self.round_price(symbol, sl_price)

    def _apply_protection_safe(self, symbol, side, type, price):
        """
        Attempts to place a protection order, migrating to Algo API if -4120 occurs.
        """
        try:
            return self.client.futures_create_order(
                symbol=symbol, side=side, type=type,
                stopPrice=price, closePosition=True, workingType='MARK_PRICE'
            )
        except Exception as e:
            if "4120" in str(e):
                print(f"[ALGO] -4120 detected for {symbol}. Migrating {type} to Algo API...")
                try:
                    # Use the official Binance Algo Order API endpoint for conditional triggers
                    return self.client.futures_create_algo_order(
                        symbol=symbol, side=side, type=type,
                        stopPrice=price, closePosition=True, workingType='MARK_PRICE',
                        algoType='CONDITIONAL'
                    )
                except Exception as e2:
                    print(f"[ALGO] Migration failed: {e2}")
                    raise e2
            raise e

    def set_tp_sl(self, symbol, side, entry_price, tp_pct, sl_pct, absolute_sl=None):
        """
        Sets TP and SL for a symbol.
        """
        exit_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        
        if side == SIDE_BUY:
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct) if absolute_sl is None else absolute_sl
        else:
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct) if absolute_sl is None else absolute_sl
            
        tp_price = self.round_price(symbol, tp_price)
        sl_price = self.round_price(symbol, sl_price)

        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            
            # Use the safe wrapper for both
            self._apply_protection_safe(symbol, exit_side, 'TAKE_PROFIT_MARKET', tp_price)
            self._apply_protection_safe(symbol, exit_side, 'STOP_MARKET', sl_price)
            
            return True
        except Exception as e:
            print(f"[EXECUTION] TP/SL Critical Error for {symbol}: {e}")
            return False
        except Exception as e:
            print(f"[EXECUTION] TP/SL Error for {symbol}: {e}")
            return False

    def manual_update_protection(self, symbol, side, tp_price, sl_price):
        """
        Manually syncs TP/SL for an existing position.
        """
        exit_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        tp_price = self.round_price(symbol, tp_price)
        sl_price = self.round_price(symbol, sl_price)
        
        try:
            # 1. Clear old protection
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            
            # 2. Set TP & SL using safe wrapper
            self._apply_protection_safe(symbol, exit_side, 'TAKE_PROFIT_MARKET', tp_price)
            self._apply_protection_safe(symbol, exit_side, 'STOP_MARKET', sl_price)
            
            return True, None
        except Exception as e:
            err = str(e)
            print(f"[EXECUTION] Manual Sync Error for {symbol}: {err}")
            # Fallback: Try setting only the SL if TP fails (often TP fails if price is too close)
            try:
                self.client.futures_create_order(
                    symbol=symbol, side=exit_side, type='STOP_MARKET',
                    stopPrice=sl_price, closePosition=True, workingType='MARK_PRICE'
                )
                return True, f"Partial Success (Only SL set): {err}"
            except:
                return False, err

    def place_atomic_trade(self, symbol, side, qty, curr_price, tp_pct, sl_pct):
        """
        Executes a sequential TRIPLE-STRIKE (Entry -> TP -> SL).
        Returns (results, error, tp_price, sl_price)
        """
        results = []
        exit_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        
        # 1. Calculate Prices
        if side == SIDE_BUY:
            tp_price = self.round_price(symbol, curr_price * (1 + tp_pct))
            sl_price = self.round_price(symbol, curr_price * (1 - sl_pct))
        else:
            tp_price = self.round_price(symbol, curr_price * (1 - tp_pct))
            sl_price = self.round_price(symbol, curr_price * (1 + sl_pct))

        try:
            # STEP 1: ENTRY (MARKET)
            print(f"[EXECUTION] Step 1: Firing ENTRY MARKET {side} for {symbol}...")
            entry = self.client.futures_create_order(
                symbol=symbol, side=side, type='MARKET', quantity=qty
            )
            results.append(entry)
            
            # STEP 2: TAKE PROFIT
            try:
                print(f"[EXECUTION] Step 2: Firing TP MARKET at {tp_price}...")
                tp = self._apply_protection_safe(symbol, exit_side, 'TAKE_PROFIT_MARKET', tp_price)
                results.append(tp)
            except Exception as e:
                print(f"[EXECUTION] TP Placement Failed on Entry: {e}")
            
            # STEP 3: STOP LOSS
            try:
                print(f"[EXECUTION] Step 3: Firing SL MARKET at {sl_price}...")
                sl = self._apply_protection_safe(symbol, exit_side, 'STOP_MARKET', sl_price)
                results.append(sl)
            except Exception as e:
                print(f"[EXECUTION] SL Placement Failed on Entry: {e}")
                
            return results, None, tp_price, sl_price
            
        except Exception as e:
            err_msg = str(e)
            print(f"[EXECUTION] Sequential Error: {err_msg}")
            # Even if TP/SL fail, we return the entry result if it exists
            return results, err_msg, tp_price, sl_price

    def verify_sl_active(self, symbol):
        try:
            open_orders = self.client.futures_get_open_orders(symbol=symbol)
            
            # Diagnostic Log: What did we find?
            order_types = [f"{o['type']}({o['side']})" for o in open_orders]
            
            has_sl = any(o['type'] in ['STOP_MARKET', 'STOP'] for o in open_orders)
            has_tp = any(o['type'] in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT'] for o in open_orders)
            
            if not (has_sl and has_tp):
                print(f"[SHIELD] Verification failed for {symbol}. Found orders: {order_types}")
            
            return has_sl and has_tp
        except Exception as e:
            print(f"[EXECUTION] SL Verification Error: {e}")
            return True # Return True on error to prevent spamming orders during API blips
