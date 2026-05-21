from binance.enums import *

def place_order(client, symbol, side, quantity):
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        return order
    except Exception as e:
        print(f"Error placing order: {e}")
        return None

def place_tp_sl(client, symbol, side, tp_price, sl_price):
    exit_side = SIDE_SELL if side == "BUY" else SIDE_BUY
    
    try:
        # Take Profit Order
        client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=tp_price,
            closePosition=True,
            workingType="CONTRACT_PRICE"
        )
        
        # Stop Loss Order
        client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=sl_price,
            closePosition=True,
            workingType="CONTRACT_PRICE"
        )
        return True
    except Exception as e:
        print(f"Error placing TP/SL: {e}")
        return False
