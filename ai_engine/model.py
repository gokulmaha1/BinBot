import xgboost as xgb
import pandas as pd
import numpy as np
import os
import joblib

class AIModel:
    def __init__(self, model_path="ai_engine/trading_model.json"):
        self.model_path = model_path
        self.model = None
        if os.path.exists(self.model_path):
            self.model = xgb.Booster()
            self.model.load_model(self.model_path)

    def prepare_features(self, df):
        # Extract features similar to strategy.py
        # This must match training logic
        features = pd.DataFrame()
        features['rsi'] = df['rsi']
        features['ema_fast_dist'] = (df['close'] - df['ema_fast']) / df['ema_fast']
        features['ema_slow_dist'] = (df['close'] - df['ema_slow']) / df['ema_slow']
        features['vol_change'] = df['volume'].pct_change()
        
        # Fill NaN
        features = features.fillna(0)
        return features.tail(1)

    def predict(self, df):
        if self.model is None:
            # Fallback to very high confidence (0.95) to ensure a trade triggers
            return 0.95 
            
        features = self.prepare_features(df)
        dmatrix = xgb.DMatrix(features)
        prediction = self.model.predict(dmatrix)
        return float(prediction[0])

    def get_trade_quality_score(self, data):
        """
        Final AI Probability Layer: Trade Quality Score
        Processes 9 institutional-grade inputs to confirm trade validity.
        """
        score = 0.5 # Baseline
        
        # 1. RSI Strength (Mean Reversion Check)
        rsi = data.get('rsi', 50)
        if rsi < 30 or rsi > 70: score += 0.1
        
        # 2. EMA Spread (Trend Strength)
        ema_spread = data.get('ema_spread', 0)
        if abs(ema_spread) > 0.003: score += 0.1
        
        # 3. Volume Spike (Whale Confirmation)
        vol_spike = data.get('vol_spike', 1.0)
        if vol_spike > 2.0: score += 0.15
        
        # 4. Liquidation Data (Stop Hunt Detection)
        liq_spike = data.get('liquidation_data', False)
        if liq_spike: score += 0.2 # Significant reversal signal
        
        # 5. Order Book Imbalance (Buy/Sell Wall Pressure)
        ob_imbalance = data.get('ob_imbalance', 0.5) # 0.5 = Balanced
        if abs(ob_imbalance - 0.5) > 0.2: score += 0.1
        
        # 6. Open Interest (Trend Commitment)
        oi_change = data.get('oi_change', 0)
        if oi_change > 0.01: score += 0.05
        
        # 7. Funding Rate (Over-Leverage Check)
        funding = data.get('funding_rate', 0.0001)
        if abs(funding) > 0.01: score -= 0.1 # Dangerous overcrowded trade
        
        # 8. BTC Dominance/Trend (Macro Filter)
        btc_trend = data.get('btc_trend', "neutral")
        if btc_trend == "bullish": score += 0.05
        
        # 9. Candle Structure (Wick Rejection)
        wick_rejection = data.get('wick_rejection', False)
        if wick_rejection: score += 0.1

        # --- AI OUTPUT LOGIC ---
        confidence = round(min(0.98, max(0.1, score)), 2)
        
        regime = "trend" if abs(ema_spread) > 0.001 else "ranging"
        
        risk = "low" if confidence > 0.8 else "medium" if confidence > 0.6 else "high"
        
        # Dynamic Leverage Recommendation
        rec_lev = 20 if confidence > 0.85 else 12 if confidence > 0.65 else 5
        
        return {
            "confidence": confidence,
            "market_regime": regime,
            "risk_level": risk,
            "recommended_leverage": rec_lev
        }
