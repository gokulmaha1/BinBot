import pandas as pd
import numpy as np
from ai_engine.model import AIModel

class HybridStrategy:
    def __init__(self):
        self.ai_model = AIModel()

    def calculate_indicators(self, df):
        # EMAs
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Bollinger Bands
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['std20'] = df['close'].rolling(window=20).std()
        df['upper_bb'] = df['sma20'] + (df['std20'] * 2)
        df['lower_bb'] = df['sma20'] - (df['std20'] * 2)
        df['bb_width'] = (df['upper_bb'] - df['lower_bb']) / df['sma20']
        
        # ADX Calculation
        df['tr'] = np.maximum(df['high'] - df['low'], 
                    np.maximum(abs(df['high'] - df['close'].shift(1)), 
                               abs(df['low'] - df['close'].shift(1))))
        df['tr14'] = df['tr'].rolling(window=14).mean()
        df['up_move'] = df['high'] - df['high'].shift(1)
        df['down_move'] = df['low'].shift(1) - df['low']
        df['pdm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
        df['ndm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)
        df['pdi'] = 100 * (df['pdm'].rolling(window=14).mean() / df['tr14'])
        df['ndi'] = 100 * (df['ndm'].rolling(window=14).mean() / df['tr14'])
        df['dx'] = 100 * (abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi']))
        df['adx'] = df['dx'].rolling(window=14).mean()

        return df

    def get_signal_with_confluence(self, df_1m, df_5m, df_15m, velocity=0, vol_spike=1.0, mom_30s=0):
        from backend.main import log 
        
        # 1. Process All Timeframes
        df_1m = self.calculate_indicators(df_1m)
        df_5m = self.calculate_indicators(df_5m)
        df_15m = self.calculate_indicators(df_15m)
        
        last_1m = df_1m.iloc[-1]
        last_5m = df_5m.iloc[-1]
        
        # 2. HOLISTIC TREND ANALYSIS (Alignment Required)
        macro_trend = "UP" if last_5m['close'] > last_5m['ema50'] else "DOWN"
        micro_trend = "UP" if last_1m['close'] > last_1m['ema21'] else "DOWN"
        adx_strong = last_1m['adx'] > 25 # Only trade if there's a real trend
        
        # 3. VOLUME & 30S MOMENTUM FILTERS
        vol_confirm = vol_spike > 1.8 # Higher threshold for surge
        mom_confirm = abs(mom_30s) > 0.0012 # Stronger 30s push
            
        rule_signal = None
        
        # --- SELECTIVE UPTREND (Both 1m & 5m must be UP) ---
        if macro_trend == "UP" and micro_trend == "UP" and adx_strong:
            # Entry: Deep Pullback (RSI < 30) OR Momentum Breakout (RSI > 60)
            if last_1m['rsi'] < 30 or (last_1m['rsi'] > 60 and mom_confirm):
                if vol_confirm or mom_confirm:
                    rule_signal = "BUY"

        # --- SELECTIVE DOWNTREND (Both 1m & 5m must be DOWN) ---
        elif macro_trend == "DOWN" and micro_trend == "DOWN" and adx_strong:
            # Entry: Overbought Bounce (RSI > 70) OR Momentum Breakdown (RSI < 40)
            if last_1m['rsi'] > 70 or (last_1m['rsi'] < 40 and mom_confirm):
                if vol_confirm or mom_confirm:
                    rule_signal = "SELL"
            
        # 4. Final Confidence Calculation
        ai_conf = 0.5
        if rule_signal == "BUY":
            ai_conf = 0.75 + (0.1 if vol_confirm else 0) + (0.1 if last_1m['adx'] > 35 else 0)
        elif rule_signal == "SELL":
            ai_conf = 0.25 - (0.1 if vol_confirm else 0) - (0.1 if last_1m['adx'] > 35 else 0)
                
        return rule_signal, ai_conf
                
        return rule_signal, ai_conf
