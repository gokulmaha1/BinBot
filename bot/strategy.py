import pandas as pd
import numpy as np
import config

def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calculate_rsi(series, length=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def generate_signal(df):
    # Calculate Indicators
    df['ema_fast'] = calculate_ema(df['close'], config.EMA_FAST)
    df['ema_slow'] = calculate_ema(df['close'], config.EMA_SLOW)
    df['ema_trend'] = calculate_ema(df['close'], 200) # Long term trend
    df['rsi'] = calculate_rsi(df['close'], config.RSI_PERIOD)

    if len(df) < 200:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Strategy: Trend Following + EMA Cross + RSI Confirmation
    # Trend Filter: Price above EMA 200 for Longs, below for Shorts
    
    # Buy: Price > EMA 200 AND Fast crosses above Slow AND RSI < Overbought
    if last['close'] > last['ema_trend']:
        if prev['ema_fast'] <= prev['ema_slow'] and last['ema_fast'] > last['ema_slow']:
            if last['rsi'] < 65: # Tighter RSI for entry
                return "BUY"

    # Sell: Price < EMA 200 AND Fast crosses below Slow AND RSI > Oversold
    elif last['close'] < last['ema_trend']:
        if prev['ema_fast'] >= prev['ema_slow'] and last['ema_fast'] < last['ema_slow']:
            if last['rsi'] > 35: # Tighter RSI for entry
                return "SELL"

    return None

