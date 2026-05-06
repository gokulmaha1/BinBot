import config

def calculate_quantity(balance, price):
    """
    Calculate position size based on risk percentage.
    Risk per trade = balance * risk_per_trade
    Quantity = Risk / Price
    """
    risk_amount = balance * config.RISK_PER_TRADE
    qty = (risk_amount * config.LEVERAGE) / price
    
    # Binance Futures requires specific precision for each symbol
    # For XRPUSDT, 1 decimal place is usually fine, but in production
    # we should ideally fetch stepSize from exchange info.
    # LABUSDT requires integer quantity (Step Size: 1)
    return int(qty)

def get_tp_sl_prices(side, entry_price):
    if side == "BUY":
        tp_price = entry_price * (1 + config.TAKE_PROFIT)
        sl_price = entry_price * (1 - config.STOP_LOSS)
    else:
        tp_price = entry_price * (1 - config.TAKE_PROFIT)
        sl_price = entry_price * (1 + config.STOP_LOSS)
        
    return round(tp_price, 4), round(sl_price, 4)
