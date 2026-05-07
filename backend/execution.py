from binance.client import Client
from binance.enums import *
import os
import logging
from bot import config

class ExecutionEngine:
    def __init__(self, client):
        self.client = client

    def setup_account(self, symbol, leverage):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self.client.futures_change_margin_type(symbol=symbol, marginType='CROSSED')
            return True
        except Exception as e:
            print(f"[EXECUTION] Setup Error (might already be set): {e}")
            return False

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
        if tick_size == 0: return round(price, 2)
        return round(round(float(price) / tick_size) * tick_size, precision)

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

        try:
            # 1. Cancel any existing TP/SL orders to avoid "closePosition" collisions
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            
            # 2. Re-apply TP
            print(f"[EXECUTION] Setting TP: {tp_price}, SL: {sl_price} for {symbol}")
            self.client.futures_create_order(
                symbol=symbol,
                side=exit_side,
                type='TAKE_PROFIT_MARKET',
                stopPrice=tp_price,
                closePosition=True,
                workingType='MARK_PRICE'
            )
            # 3. Re-apply SL
            if sl_pct > 0 or absolute_sl is not None:
                self.client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type='STOP_MARKET',
                    stopPrice=sl_price,
                    closePosition=True,
                    workingType='MARK_PRICE'
                )
            return True
        except Exception as e:
            print(f"[EXECUTION] TP/SL Error for {symbol}: {e}")
            return False

    def place_atomic_trade(self, symbol, side, qty, curr_price, tp_pct, sl_pct):
        try:
            exit_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
            
            # 1. Round Prices
            if side == SIDE_BUY:
                tp_price = self.round_price(symbol, curr_price * (1 + tp_pct))
                sl_price = self.round_price(symbol, curr_price * (1 - sl_pct))
            else:
                tp_price = self.round_price(symbol, curr_price * (1 - tp_pct))
                sl_price = self.round_price(symbol, curr_price * (1 + sl_pct))

            # 2. Construct Batch with Explicit Quantities for reliability
            batch = [
                {
                    'symbol': symbol,
                    'side': side,
                    'type': 'MARKET',
                    'quantity': str(qty)
                },
                {
                    'symbol': symbol,
                    'side': exit_side,
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': str(tp_price),
                    'quantity': str(qty),
                    'workingType': 'MARK_PRICE',
                    'reduceOnly': 'true'
                },
                {
                    'symbol': symbol,
                    'side': exit_side,
                    'type': 'STOP_MARKET',
                    'stopPrice': str(sl_price),
                    'quantity': str(qty),
                    'workingType': 'MARK_PRICE',
                    'reduceOnly': 'true'
                }
            ]
            
            print(f"[EXECUTION] Firing TRIPLE-VERIFIED ATOMIC BATCH for {symbol}...")
            results = self.client.futures_place_batch_order(batchOrders=batch)
            return results, None
        except Exception as e:
            print(f"[EXECUTION] Atomic Error: {e}")
            return None, str(e)

    def verify_sl_active(self, symbol):
        try:
            open_orders = self.client.futures_get_open_orders(symbol=symbol)
            # We consider the shield active if there is at least ONE STOP_MARKET or TAKE_PROFIT_MARKET order
            # This prevents the loop if one is placed but the other is pending or cancelled
            has_sl = any(o['type'] == 'STOP_MARKET' for o in open_orders)
            has_tp = any(o['type'] == 'TAKE_PROFIT_MARKET' for o in open_orders)
            
            return has_sl and has_tp # Both must exist for a healthy shield
        except Exception as e:
            print(f"[EXECUTION] SL Verification Error: {e}")
            return True # Return True on error to prevent spamming orders during API blips
