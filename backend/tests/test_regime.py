import pytest
from app.engine.regime import RegimeDetector, MarketRegime, VolatilityClass
from app.engine.features import FeatureSet

def test_trending_bullish():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=100.0,
        ema_fast=105.0,  # 9
        ema_mid=103.0,   # 21
        ema_slow=101.0,  # 50
        ema_trend=95.0,  # 200
        adx=30.0,
        supertrend_direction=1,
        supertrend=98.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.TRENDING_BULLISH.value
    assert result.confidence > 0.8
    assert result.trend_strength > 0.5

def test_trending_bearish():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=90.0,
        ema_fast=85.0,
        ema_mid=87.0,
        ema_slow=89.0,
        ema_trend=95.0,
        adx=35.0,
        supertrend_direction=-1,
        supertrend=92.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.TRENDING_BEARISH.value
    assert result.confidence > 0.8

def test_ranging():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=100.0,
        bb_upper=102.0,
        bb_middle=100.0,
        bb_lower=98.0,
        bb_width=0.02,
        bb_percent_b=0.5,
        adx=15.0,
        atr=1.0,
        atr_avg_20=1.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.RANGING.value

def test_high_volatility():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=100.0,
        atr=5.0,
        atr_avg_20=2.0,  # ratio = 2.5 >= 2.0
        adx=30.0,
        bb_width=0.1,
        bb_upper=110.0,
        bb_lower=90.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.HIGH_VOLATILITY.value
    assert result.volatility_class == VolatilityClass.HIGH.value

def test_low_volatility():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=100.0,
        atr=0.4,
        atr_avg_20=1.0,  # ratio = 0.4 <= 0.5
        adx=30.0,
        bb_width=0.1,
        bb_upper=102.0,
        bb_lower=98.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.LOW_VOLATILITY.value
    assert result.volatility_class == VolatilityClass.LOW.value

def test_breakout_resistance():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=105.0,
        nearest_resistance=100.0,
        nearest_support=90.0,
        volume_spike_ratio=2.0,
        bb_upper=103.0,
        bb_lower=97.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.BREAKOUT.value

def test_mean_reversion_overbought():
    detector = RegimeDetector()
    f = FeatureSet(
        symbol="BTCUSDT",
        timestamp="0",
        close=100.0,
        rsi=82.0,
        bb_percent_b=0.98,
        stoch_rsi_k=90.0,
        adx=18.0
    )
    result = detector.detect(f)
    assert result.regime == MarketRegime.MEAN_REVERSION.value
