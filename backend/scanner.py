import pandas as pd
from binance.client import Client
import config

class MarketScanner:
    def __init__(self, client: Client):
        self.client = client

    async def get_top_pairs(self, limit=10):
        """Fetches top futures pairs by 24h volume asynchronously."""
        from backend.main import log
        try:
            log("Alpha Scanner: Fetching market-wide tickers...", "info")
            # Using synchronous call but in a non-blocking way for the event loop
            tickers = self.client.futures_ticker() 
            sorted_tickers = sorted(tickers, key=lambda x: float(x['quoteVolume']), reverse=True)
            
            pairs = []
            for t in sorted_tickers:
                symbol = t['symbol']
                if symbol.endswith('USDT') and 'USDC' not in symbol and 'BTCDOM' not in symbol:
                    pairs.append(symbol)
                    if len(pairs) >= limit:
                        break
            return pairs
        except Exception as e:
            log(f"Scanner Fetch Error: {e}", "error")
            return [config.SYMBOL]

    async def rank_pairs(self, pairs):
        """Ranks pairs based on Trend (EMA) and Volatility (ATR)."""
        from backend.main import log
        ranked = []
        log(f"Alpha Scanner: Ranking {len(pairs)} candidates...", "info")
        
        for symbol in pairs:
            try:
                # Fetch 5m Klines (Reduced limit for speed)
                klines = self.client.futures_klines(symbol=symbol, interval='5m', limit=30)
                df = pd.DataFrame(klines, columns=['time','open','high','low','close','vol','ct','qv','nt','tb','tv','ig'])
                for col in ['open', 'high', 'low', 'close', 'vol']:
                    df[col] = df[col].astype(float)

                # 1. Trend Score
                ema9 = df['close'].ewm(span=9).mean().iloc[-1]
                ema21 = df['close'].ewm(span=21).mean().iloc[-1]
                ema50 = df['close'].ewm(span=50).mean().iloc[-1]
                
                trend_score = 0
                if ema9 > ema21 > ema50: trend_score = 10 
                if ema9 < ema21 < ema50: trend_score = 10 

                # 2. Volatility Score
                high_low = df['high'] - df['low']
                atr = high_low.rolling(14).mean().iloc[-1]
                vol_pct = (atr / df['close'].iloc[-1]) * 100
                
                vol_score = 0
                if 0.15 <= vol_pct <= 1.0: # Relaxed for DOGE/PEPE/etc.
                    vol_score = 10

                total_score = trend_score + vol_score
                if total_score >= 15:
                    ranked.append({'symbol': symbol, 'score': total_score, 'vol_pct': vol_pct})
            except Exception as e:
                continue
                
        log(f"Alpha Scanner: Found {len(ranked)} viable opportunities.", "info")
        return sorted(ranked, key=lambda x: x['score'], reverse=True)
