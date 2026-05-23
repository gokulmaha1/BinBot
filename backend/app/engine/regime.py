"""
BinBot AI Auto Mode — Market Regime Detection Engine
Classifies current market state to guide strategy selection.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from app.config import settings
from app.engine.features import FeatureSet

logger = logging.getLogger(__name__)


# ── Regime Enum ──────────────────────────────────────────────────

class MarketRegime(str, Enum):
    """Possible market regimes."""
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    UNKNOWN = "UNKNOWN"


class VolatilityClass(str, Enum):
    """Volatility classification."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


# ── Result dataclass ─────────────────────────────────────────────

@dataclass
class RegimeResult:
    """Result of regime detection."""
    regime: str
    confidence: float        # 0.0 – 1.0
    volatility_class: str
    trend_strength: float    # 0.0 – 1.0


# ── Thresholds ───────────────────────────────────────────────────

ADX_TRENDING: float = 25.0
ADX_RANGING: float = 20.0
ATR_HIGH_MULT: float = 2.0
ATR_LOW_MULT: float = 0.5
RSI_OVERBOUGHT: float = 70.0
RSI_OVERSOLD: float = 30.0
VOLUME_SPIKE_BREAKOUT: float = 1.5  # 1.5× average volume


class RegimeDetector:
    """Detects the current market regime from extracted features."""

    def detect(self, features: FeatureSet) -> RegimeResult:
        """
        Analyse features and return the dominant regime.

        The detector evaluates multiple regime candidates,
        assigns a confidence to each, and returns the strongest match.

        Parameters
        ----------
        features : FeatureSet
            Pre-computed technical features.

        Returns
        -------
        RegimeResult
        """
        candidates: list[tuple[str, float]] = []

        # ── Evaluate each regime ────────────────────────────────
        bull_conf = self._check_trending_bullish(features)
        if bull_conf > 0:
            candidates.append((MarketRegime.TRENDING_BULLISH.value, bull_conf))

        bear_conf = self._check_trending_bearish(features)
        if bear_conf > 0:
            candidates.append((MarketRegime.TRENDING_BEARISH.value, bear_conf))

        range_conf = self._check_ranging(features)
        if range_conf > 0:
            candidates.append((MarketRegime.RANGING.value, range_conf))

        hvol_conf = self._check_high_volatility(features)
        if hvol_conf > 0:
            candidates.append((MarketRegime.HIGH_VOLATILITY.value, hvol_conf))

        lvol_conf = self._check_low_volatility(features)
        if lvol_conf > 0:
            candidates.append((MarketRegime.LOW_VOLATILITY.value, lvol_conf))

        brk_conf = self._check_breakout(features)
        if brk_conf > 0:
            candidates.append((MarketRegime.BREAKOUT.value, brk_conf))

        mr_conf = self._check_mean_reversion(features)
        if mr_conf > 0:
            candidates.append((MarketRegime.MEAN_REVERSION.value, mr_conf))

        # ── Select the highest-confidence regime ────────────────
        if not candidates:
            regime = MarketRegime.UNKNOWN.value
            confidence = 0.0
        else:
            candidates.sort(key=lambda x: x[1], reverse=True)
            regime, confidence = candidates[0]

        vol_class = self._classify_volatility(features)
        trend_str = self._compute_trend_strength(features)

        result = RegimeResult(
            regime=regime,
            confidence=round(confidence, 4),
            volatility_class=vol_class,
            trend_strength=round(trend_str, 4),
        )

        logger.info(
            "Regime detected for %s: %s (confidence=%.2f, vol=%s, trend=%.2f)",
            features.symbol, result.regime, result.confidence,
            result.volatility_class, result.trend_strength,
        )
        return result

    # ── Private regime checks ────────────────────────────────────

    def _check_trending_bullish(self, f: FeatureSet) -> float:
        """
        TRENDING_BULLISH: EMA9 > EMA21 > EMA50 > EMA200, ADX > 25, price > EMAs.
        """
        score = 0.0
        total = 6

        # EMA stack alignment
        if f.ema_fast > f.ema_mid:
            score += 1
        if f.ema_mid > f.ema_slow:
            score += 1
        if f.ema_slow > f.ema_trend:
            score += 1

        # Price Action relative to EMAs (prevent lagging false positives)
        if f.close > f.ema_fast:
            score += 1
        elif f.close > f.ema_mid:
            score += 0.5

        # ADX strength
        if f.adx > ADX_TRENDING:
            score += 1
        elif f.adx > ADX_RANGING:
            score += 0.5

        # Supertrend confirmation
        if f.supertrend_direction == 1:
            score += 1
        elif f.close > f.supertrend:
            score += 0.5

        return score / total

    def _check_trending_bearish(self, f: FeatureSet) -> float:
        """
        TRENDING_BEARISH: EMA9 < EMA21 < EMA50 < EMA200, ADX > 25, price < EMAs.
        """
        score = 0.0
        total = 6

        if f.ema_fast < f.ema_mid:
            score += 1
        if f.ema_mid < f.ema_slow:
            score += 1
        if f.ema_slow < f.ema_trend:
            score += 1

        # Price Action relative to EMAs (prevent lagging false positives)
        if f.close < f.ema_fast:
            score += 1
        elif f.close < f.ema_mid:
            score += 0.5

        if f.adx > ADX_TRENDING:
            score += 1
        elif f.adx > ADX_RANGING:
            score += 0.5

        if f.supertrend_direction == -1:
            score += 1
        elif f.close < f.supertrend:
            score += 0.5

        return score / total

    def _check_ranging(self, f: FeatureSet) -> float:
        """
        RANGING: ADX < 20, price inside Bollinger Bands.
        """
        score = 0.0
        total = 4

        # Low ADX
        if f.adx < ADX_RANGING:
            score += 1.5
        elif f.adx < ADX_TRENDING:
            score += 0.5

        # Price inside BB
        if f.bb_lower < f.close < f.bb_upper:
            score += 1

        # %B near middle
        if 0.2 < f.bb_percent_b < 0.8:
            score += 0.5

        # Narrow BB width indicates ranging
        if f.bb_width < 0.03:
            score += 1

        return min(score / total, 1.0)

    def _check_high_volatility(self, f: FeatureSet) -> float:
        """
        HIGH_VOLATILITY: ATR > 2× 20-period average ATR.
        """
        if f.atr_avg_20 == 0:
            return 0.0

        ratio = f.atr / f.atr_avg_20
        if ratio >= ATR_HIGH_MULT:
            # Scale confidence by how far above threshold
            conf = min(0.5 + (ratio - ATR_HIGH_MULT) * 0.25, 1.0)
            return conf
        return 0.0

    def _check_low_volatility(self, f: FeatureSet) -> float:
        """
        LOW_VOLATILITY: ATR < 0.5× 20-period average ATR.
        """
        if f.atr_avg_20 == 0:
            return 0.0

        ratio = f.atr / f.atr_avg_20
        if ratio <= ATR_LOW_MULT:
            conf = min(0.5 + (ATR_LOW_MULT - ratio) * 0.5, 1.0)
            return conf
        return 0.0

    def _check_breakout(self, f: FeatureSet) -> float:
        """
        BREAKOUT: price breaks support/resistance + volume spike.
        """
        score = 0.0
        total = 3

        # Price breaking resistance
        if f.nearest_resistance > 0 and f.close > f.nearest_resistance:
            score += 1
        # Price breaking support (bearish breakout)
        elif f.nearest_support > 0 and f.close < f.nearest_support:
            score += 1

        # Volume confirmation
        if f.volume_spike_ratio >= VOLUME_SPIKE_BREAKOUT:
            score += 1
        elif f.volume_spike_ratio >= 1.2:
            score += 0.5

        # Bollinger Band breakout
        if f.close > f.bb_upper or f.close < f.bb_lower:
            score += 1

        if score == 0:
            return 0.0
        return score / total

    def _check_mean_reversion(self, f: FeatureSet) -> float:
        """
        MEAN_REVERSION: RSI extreme + price at Bollinger Band edge.
        """
        score = 0.0
        total = 4

        # RSI extreme
        if f.rsi >= RSI_OVERBOUGHT or f.rsi <= RSI_OVERSOLD:
            score += 1.5
        elif f.rsi >= 65 or f.rsi <= 35:
            score += 0.5

        # Price at BB edge
        if f.bb_percent_b >= 0.95 or f.bb_percent_b <= 0.05:
            score += 1.5
        elif f.bb_percent_b >= 0.85 or f.bb_percent_b <= 0.15:
            score += 0.75

        # Stoch RSI overbought/oversold
        if f.stoch_rsi_k >= 80 or f.stoch_rsi_k <= 20:
            score += 0.5

        # Low ADX (range-bound needed for mean reversion)
        if f.adx < ADX_TRENDING:
            score += 0.5

        if score == 0:
            return 0.0
        return min(score / total, 1.0)

    # ── Volatility classification ────────────────────────────────

    @staticmethod
    def _classify_volatility(f: FeatureSet) -> str:
        """Classify volatility into LOW / NORMAL / HIGH / EXTREME."""
        if f.atr_avg_20 == 0:
            return VolatilityClass.NORMAL.value

        ratio = f.atr / f.atr_avg_20
        if ratio >= 3.0:
            return VolatilityClass.EXTREME.value
        if ratio >= ATR_HIGH_MULT:
            return VolatilityClass.HIGH.value
        if ratio <= ATR_LOW_MULT:
            return VolatilityClass.LOW.value
        return VolatilityClass.NORMAL.value

    # ── Trend strength ───────────────────────────────────────────

    @staticmethod
    def _compute_trend_strength(f: FeatureSet) -> float:
        """
        Compute a normalised 0-1 trend strength from ADX and EMA alignment.
        """
        score = 0.0

        # ADX contribution (0 – 0.5)
        adx_norm = min(f.adx / 50.0, 1.0)
        score += adx_norm * 0.5

        # EMA alignment contribution (0 – 0.3)
        bullish = (
            (f.ema_fast > f.ema_mid)
            and (f.ema_mid > f.ema_slow)
            and (f.ema_slow > f.ema_trend)
        )
        bearish = (
            (f.ema_fast < f.ema_mid)
            and (f.ema_mid < f.ema_slow)
            and (f.ema_slow < f.ema_trend)
        )
        if bullish or bearish:
            score += 0.3
        elif (f.ema_fast > f.ema_mid > f.ema_slow) or (
            f.ema_fast < f.ema_mid < f.ema_slow
        ):
            score += 0.15

        # Supertrend alignment (0 – 0.2)
        if bullish and f.supertrend_direction == 1:
            score += 0.2
        elif bearish and f.supertrend_direction == -1:
            score += 0.2
        elif f.supertrend_direction != 0:
            score += 0.1

        return min(score, 1.0)
