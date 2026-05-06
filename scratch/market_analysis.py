import os
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

def analyze_market(symbol="LABUSDT"):
    load_dotenv()
    client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
    
    # Fetch 15m data for trend analysis
    klines = client.futures_klines(symbol=symbol, interval="15m", limit=500)
    df = pd.DataFrame(klines, columns=['time','open','high','low','close','vol','ct','qv','nt','tb','tv','ig'])
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    
    # Calculate Volatility (ATR)
    df['tr'] = df['high'] - df['low']
    atr = df['tr'].rolling(14).mean().iloc[-1]
    
    # Calculate Trend
    ema200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1]
    current_price = df['close'].iloc[-1]
    
    # Calculate RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1]

    print(f"--- MARKET ANALYSIS: {symbol} ---")
    print(f"Current Price: {current_price}")
    print(f"EMA 200: {ema200}")
    print(f"Trend: {'BULLISH' if current_price > ema200 else 'BEARISH'}")
    print(f"RSI: {current_rsi:.2f}")
    print(f"Volatility (ATR): {atr:.4f}")
    
    # Suggestion
    if current_price > ema200 and current_rsi < 40:
        print("SUGGESTION: Huge Buy Signal (Deep Pullback in Uptrend)")
    elif current_price < ema200 and current_rsi > 60:
        print("SUGGESTION: Huge Sell Signal (Relief Rally in Downtrend)")
    else:
        print("SUGGESTION: Wait for clear setup on 15m timeframe for major trend reversal.")

if __name__ == "__main__":
    analyze_market()
