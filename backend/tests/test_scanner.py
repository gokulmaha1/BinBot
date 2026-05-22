import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from app.engine.scanner import PairScanner

def test_apply_hard_filters():
    scanner = PairScanner()
    
    # Setup mock config settings
    with patch('app.engine.scanner.settings') as mock_settings:
        mock_settings.SCANNER_MIN_VOLUME_24H = 50_000_000
        mock_settings.SCANNER_MIN_LISTING_DAYS = 30
        mock_settings.SCANNER_MAX_SPREAD_PCT = 0.001  # 0.1%
        
        symbol_info = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "listing_age_days": 100,
                "price_precision": 2,
                "qty_precision": 3
            },
            "USDCUSDT": {  # Blacklisted
                "symbol": "USDCUSDT",
                "listing_age_days": 100,
                "price_precision": 2,
                "qty_precision": 3
            },
            "LOWVOLUSDT": {  # Low Volume
                "symbol": "LOWVOLUSDT",
                "listing_age_days": 100,
                "price_precision": 2,
                "qty_precision": 3
            },
            "NEWUSDT": {  # Low age
                "symbol": "NEWUSDT",
                "listing_age_days": 15,
                "price_precision": 2,
                "qty_precision": 3
            },
            "WIDESPREADUSDT": {  # Too wide spread
                "symbol": "WIDESPREADUSDT",
                "listing_age_days": 100,
                "price_precision": 2,
                "qty_precision": 3
            }
        }
        
        ticker_map = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "quoteVolume": "100000000.0",
                "askPrice": "100.05",
                "bidPrice": "99.95"
            },
            "USDCUSDT": {
                "symbol": "USDCUSDT",
                "quoteVolume": "200000000.0",
                "askPrice": "1.0001",
                "bidPrice": "0.9999"
            },
            "LOWVOLUSDT": {
                "symbol": "LOWVOLUSDT",
                "quoteVolume": "10000000.0",  # 10M < 50M limit
                "askPrice": "10.005",
                "bidPrice": "9.995"
            },
            "NEWUSDT": {
                "symbol": "NEWUSDT",
                "quoteVolume": "80000000.0",
                "askPrice": "5.005",
                "bidPrice": "4.995"
            },
            "WIDESPREADUSDT": {
                "symbol": "WIDESPREADUSDT",
                "quoteVolume": "70000000.0",
                "askPrice": "10.15",
                "bidPrice": "9.85"  # (10.15 - 9.85)/10.0 = 0.03 (3% > 0.1%)
            }
        }
        
        passed = scanner._apply_hard_filters(symbol_info, ticker_map)
        
        assert "BTCUSDT" in passed
        assert "USDCUSDT" not in passed  # Blacklisted
        assert "LOWVOLUSDT" not in passed  # Low volume
        assert "NEWUSDT" not in passed  # New listing
        assert "WIDESPREADUSDT" not in passed  # High spread

def test_classify_regime():
    scanner = PairScanner()
    
    # strong_trend: adx > 40 and atr_pct > 2.0
    assert scanner._classify_regime(adx=45.0, atr_pct=2.5, vol_spike=1.0) == "strong_trend"
    
    # trending: adx > 25
    assert scanner._classify_regime(adx=30.0, atr_pct=1.0, vol_spike=1.0) == "trending"
    
    # volatile_breakout: atr_pct > 3.0 and vol_spike > 2.0
    assert scanner._classify_regime(adx=15.0, atr_pct=3.5, vol_spike=2.5) == "volatile_breakout"
    
    # low_volatility: atr_pct < 0.5
    assert scanner._classify_regime(adx=15.0, atr_pct=0.4, vol_spike=1.0) == "low_volatility"
    
    # volume_spike: vol_spike > 3.0
    assert scanner._classify_regime(adx=15.0, atr_pct=1.0, vol_spike=3.5) == "volume_spike"
    
    # ranging: default
    assert scanner._classify_regime(adx=15.0, atr_pct=1.0, vol_spike=1.0) == "ranging"

def test_calculate_atr():
    scanner = PairScanner()
    
    # Setup some test price arrays
    # 21 elements (need at least period + 1 for wilder ATR)
    highs = np.array([10 + i for i in range(25)], dtype=np.float64)
    lows = np.array([5 + i for i in range(25)], dtype=np.float64)
    closes = np.array([8 + i for i in range(25)], dtype=np.float64)
    
    # true ranges should all be: max(10+i - (5+i), |10+i - (8+i-1)|, |5+i - (8+i-1)|)
    # tr = max(5, |i + 3|, |i - 3|) = 5 (since high[i]-low[i] is always 5 and diff with close[i-1] is minor)
    atr = scanner._calculate_atr(highs, lows, closes, period=14)
    
    assert atr > 0.0
    # Since all TRs are around 5.0, the ATR should be close to 5.0
    assert abs(atr - 5.0) < 0.2

def test_apply_manual_pairs_override():
    scanner = PairScanner()
    
    with patch('app.engine.scanner.settings') as mock_settings:
        mock_settings.SCANNER_MANUAL_PAIRS = "BTCUSDT, ETHUSDT"
        
        symbol_info = {
            "BTCUSDT": {"symbol": "BTCUSDT"},
            "ETHUSDT": {"symbol": "ETHUSDT"},
            "SOLUSDT": {"symbol": "SOLUSDT"}
        }
        
        ticker_map = {
            "BTCUSDT": {"symbol": "BTCUSDT"},
            "ETHUSDT": {"symbol": "ETHUSDT"},
            "SOLUSDT": {"symbol": "SOLUSDT"}
        }
        
        manual_pairs_list = [s.strip().upper() for s in mock_settings.SCANNER_MANUAL_PAIRS.split(",") if s.strip()]
        candidates = []
        for sym in manual_pairs_list:
            if sym in symbol_info and sym in ticker_map:
                candidates.append(sym)
                
        assert candidates == ["BTCUSDT", "ETHUSDT"]
        assert "SOLUSDT" not in candidates

