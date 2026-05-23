"""
BinBot AI Auto Mode — Strategy Selection Engine
Five auto-selected strategies filtered by market regime.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.engine.features import FeatureSet
from app.engine.regime import MarketRegime, RegimeResult

logger = logging.getLogger(__name__)


# ── Strategy names ───────────────────────────────────────────────

class StrategyName(str, Enum):
    """Canonical strategy identifiers."""
    TREND_FOLLOWING = "trend_following"
    MOMENTUM_BREAKOUT = "momentum_breakout"
    MEAN_REVERSION = "mean_reversion"
    SCALPING = "scalping"
    VOLATILITY_EXPANSION = "volatility_expansion"


# ── Regime → Strategy compatibility map ──────────────────────────

REGIME_STRATEGY_MAP: dict[str, list[str]] = {
    MarketRegime.TRENDING_BULLISH.value: [
        StrategyName.TREND_FOLLOWING.value,
        StrategyName.MOMENTUM_BREAKOUT.value,
    ],
    MarketRegime.TRENDING_BEARISH.value: [
        StrategyName.TREND_FOLLOWING.value,
        StrategyName.MOMENTUM_BREAKOUT.value,
    ],
    MarketRegime.RANGING.value: [
        StrategyName.MEAN_REVERSION.value,
        StrategyName.SCALPING.value,
    ],
    MarketRegime.HIGH_VOLATILITY.value: [
        StrategyName.VOLATILITY_EXPANSION.value,
        StrategyName.MOMENTUM_BREAKOUT.value,
    ],
    MarketRegime.LOW_VOLATILITY.value: [
        StrategyName.SCALPING.value,
        StrategyName.MEAN_REVERSION.value,
    ],
    MarketRegime.BREAKOUT.value: [
        StrategyName.MOMENTUM_BREAKOUT.value,
        StrategyName.VOLATILITY_EXPANSION.value,
    ],
    MarketRegime.MEAN_REVERSION.value: [
        StrategyName.MEAN_REVERSION.value,
        StrategyName.SCALPING.value,
    ],
}


# ── Data classes ─────────────────────────────────────────────────

@dataclass
class StrategySignal:
    """Output of a single strategy evaluation."""
    side: str                     # "BUY" or "SELL"
    strategy_name: str
    confidence: float             # 0.0 – 1.0
    entry_price: float
    sl_distance: float            # absolute distance from entry to stop loss
    features_used: dict = field(default_factory=dict)


# ── Constants ────────────────────────────────────────────────────

RSI_BULL_MIN: float = 40.0
RSI_BULL_MAX: float = 70.0
RSI_BEAR_MIN: float = 30.0
RSI_BEAR_MAX: float = 60.0
RSI_OB: float = 70.0
RSI_OS: float = 30.0
STOCH_OB: float = 80.0
STOCH_OS: float = 20.0
VOLUME_CONFIRM_RATIO: float = 1.2
SL_ATR_MULTIPLIER: float = 1.5


class StrategyEngine:
    """Evaluates all compatible strategies and returns qualifying signals."""

    def evaluate(
        self, features: FeatureSet, regime: RegimeResult
    ) -> list[StrategySignal]:
        """
        Run every strategy compatible with the detected regime.

        Parameters
        ----------
        features : FeatureSet
        regime : RegimeResult

        Returns
        -------
        list[StrategySignal]
            Signals from strategies that fire, sorted by confidence desc.
        """
        compatible = REGIME_STRATEGY_MAP.get(regime.regime, [])
        if not compatible:
            logger.info(
                "No strategies mapped for regime %s on %s",
                regime.regime, features.symbol,
            )
            return []

        dispatch: dict[str, callable] = {
            StrategyName.TREND_FOLLOWING.value: self._trend_following,
            StrategyName.MOMENTUM_BREAKOUT.value: self._momentum_breakout,
            StrategyName.MEAN_REVERSION.value: self._mean_reversion,
            StrategyName.SCALPING.value: self._scalping,
            StrategyName.VOLATILITY_EXPANSION.value: self._volatility_expansion,
        }

        signals: list[StrategySignal] = []
        for name in compatible:
            fn = dispatch.get(name)
            if fn is None:
                continue
            try:
                signal = fn(features, regime)
                if signal is not None:
                    signals.append(signal)
                    logger.info(
                        "Strategy %s fired for %s: side=%s conf=%.2f",
                        name, features.symbol, signal.side, signal.confidence,
                    )
            except Exception:
                logger.exception("Strategy %s error for %s", name, features.symbol)

        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals

    # ── Strategy 1: Trend Following ──────────────────────────────

    def _trend_following(
        self, f: FeatureSet, regime: RegimeResult
    ) -> StrategySignal | None:
        """
        Best for TRENDING_BULLISH / TRENDING_BEARISH.

        Entry on EMA9 > EMA21 > EMA50 crossover, confirmed by RSI,
        VWAP alignment, and volume.
        """
        score = 0.0
        total = 8 # Increased total required confluence factors
        side: str | None = None

        # ── Bullish setup ────────────────────────────────────────
        bull_ema = (
            f.close > f.ema_fast > f.ema_mid > f.ema_slow > f.ema_trend
        )
        bear_ema = (
            f.close < f.ema_fast < f.ema_mid < f.ema_slow < f.ema_trend
        )

        # Require minimum trend strength
        if f.adx < 25.0:
            return None

        if bull_ema:
            side = "BUY"
            score += 3.0

            # RSI confirmation: not overbought, > 40
            if 50 <= f.rsi <= 70:
                score += 1.0

            # VWAP: price above VWAP in uptrend
            if f.close > f.vwap:
                score += 1.0

            # Supertrend confirmation
            if f.supertrend_direction == 1:
                score += 1.0

            # MACD confirmation (Strong momentum)
            if f.macd_histogram > 0 and f.macd_line > f.macd_signal:
                score += 2.0

        elif bear_ema:
            side = "SELL"
            score += 3.0

            if 30 <= f.rsi <= 50:
                score += 1.0
            if f.close < f.vwap:
                score += 1.0
            if f.supertrend_direction == -1:
                score += 1.0
            if f.macd_histogram < 0 and f.macd_line < f.macd_signal:
                score += 2.0
        else:
            return None

        # SNIPER MODE: Require perfect 8/8 score to execute a trade
        confidence = score / total
        if confidence < 1.0:
            return None

        sl_dist = f.atr * SL_ATR_MULTIPLIER

        return StrategySignal(
            side=side,
            strategy_name=StrategyName.TREND_FOLLOWING.value,
            confidence=round(confidence, 4),
            entry_price=f.close,
            sl_distance=round(sl_dist, 8),
            features_used={
                "ema_fast": f.ema_fast,
                "ema_mid": f.ema_mid,
                "ema_slow": f.ema_slow,
                "rsi": f.rsi,
                "vwap": f.vwap,
                "volume_spike_ratio": f.volume_spike_ratio,
                "supertrend_dir": f.supertrend_direction,
            },
        )

    # ── Strategy 2: Momentum Breakout ────────────────────────────

    def _momentum_breakout(
        self, f: FeatureSet, regime: RegimeResult
    ) -> StrategySignal | None:
        """
        Best for BREAKOUT regime.

        Entry on support/resistance break with volume spike + MACD confirmation.
        """
        score = 0.0
        total = 5
        side: str | None = None

        # ── Resistance breakout ──────────────────────────────────
        if f.nearest_resistance > 0 and f.close > f.nearest_resistance:
            side = "BUY"
            score += 1.5

            # MACD confirmation (histogram positive & rising)
            if f.macd_histogram > 0:
                score += 1.0
            if f.macd_line > f.macd_signal:
                score += 0.5

        # ── Support breakdown ────────────────────────────────────
        elif f.nearest_support > 0 and f.close < f.nearest_support:
            side = "SELL"
            score += 1.5

            if f.macd_histogram < 0:
                score += 1.0
            if f.macd_line < f.macd_signal:
                score += 0.5

        else:
            return None

        # Volume spike confirmation
        if f.volume_spike_ratio >= 1.5:
            score += 1.5
        elif f.volume_spike_ratio >= VOLUME_CONFIRM_RATIO:
            score += 0.75

        # Bollinger Band breakout
        if side == "BUY" and f.close > f.bb_upper:
            score += 0.5
        elif side == "SELL" and f.close < f.bb_lower:
            score += 0.5

        confidence = score / total
        if confidence < 0.4:
            return None

        sl_dist = f.atr * SL_ATR_MULTIPLIER

        return StrategySignal(
            side=side,
            strategy_name=StrategyName.MOMENTUM_BREAKOUT.value,
            confidence=round(confidence, 4),
            entry_price=f.close,
            sl_distance=round(sl_dist, 8),
            features_used={
                "nearest_resistance": f.nearest_resistance,
                "nearest_support": f.nearest_support,
                "macd_histogram": f.macd_histogram,
                "macd_line": f.macd_line,
                "macd_signal": f.macd_signal,
                "volume_spike_ratio": f.volume_spike_ratio,
                "bb_upper": f.bb_upper,
                "bb_lower": f.bb_lower,
            },
        )

    # ── Strategy 3: Mean Reversion ───────────────────────────────

    def _mean_reversion(
        self, f: FeatureSet, regime: RegimeResult
    ) -> StrategySignal | None:
        """
        Best for MEAN_REVERSION / RANGING.

        Entry outside BB with RSI extreme + Stochastic RSI confirmation.
        """
        score = 0.0
        total = 5
        side: str | None = None

        # ── Oversold → Buy ───────────────────────────────────────
        if f.rsi <= RSI_OS:
            side = "BUY"
            score += 1.5

            # Price at or below lower BB
            if f.bb_percent_b <= 0.05:
                score += 1.5
            elif f.bb_percent_b <= 0.15:
                score += 0.75

            # Stochastic RSI confirmation
            if f.stoch_rsi_k <= STOCH_OS:
                score += 1.0
            # K crossing above D (bullish crossover)
            if f.stoch_rsi_k > f.stoch_rsi_d:
                score += 0.5

            # Mean target exists (VWAP or BB middle)
            if f.close < f.vwap:
                score += 0.5

        # ── Overbought → Sell ────────────────────────────────────
        elif f.rsi >= RSI_OB:
            side = "SELL"
            score += 1.5

            if f.bb_percent_b >= 0.95:
                score += 1.5
            elif f.bb_percent_b >= 0.85:
                score += 0.75

            if f.stoch_rsi_k >= STOCH_OB:
                score += 1.0
            if f.stoch_rsi_k < f.stoch_rsi_d:
                score += 0.5

            if f.close > f.vwap:
                score += 0.5
        else:
            return None

        confidence = score / total
        if confidence < 0.4:
            return None

        sl_dist = f.atr * SL_ATR_MULTIPLIER

        return StrategySignal(
            side=side,
            strategy_name=StrategyName.MEAN_REVERSION.value,
            confidence=round(confidence, 4),
            entry_price=f.close,
            sl_distance=round(sl_dist, 8),
            features_used={
                "rsi": f.rsi,
                "bb_percent_b": f.bb_percent_b,
                "stoch_rsi_k": f.stoch_rsi_k,
                "stoch_rsi_d": f.stoch_rsi_d,
                "vwap": f.vwap,
                "bb_upper": f.bb_upper,
                "bb_lower": f.bb_lower,
            },
        )

    # ── Strategy 4: Scalping ─────────────────────────────────────

    def _scalping(
        self, f: FeatureSet, regime: RegimeResult
    ) -> StrategySignal | None:
        """
        Best for RANGING / LOW_VOLATILITY.

        Uses micro EMA cross (fast vs mid), order-flow imbalance via
        OBV slope, and tight BB for ranging confirmation.
        """
        score = 0.0
        total = 5
        side: str | None = None

        # ── Micro EMA cross ──────────────────────────────────────
        if f.ema_fast > f.ema_mid and f.close > f.ema_fast:
            side = "BUY"
            score += 1.5
        elif f.ema_fast < f.ema_mid and f.close < f.ema_fast:
            side = "SELL"
            score += 1.5
        else:
            return None

        # ── OBV slope as order-flow proxy ────────────────────────
        if side == "BUY" and f.obv_slope > 0:
            score += 1.0
        elif side == "SELL" and f.obv_slope < 0:
            score += 1.0

        # RSI mid-range (not extreme — scalping avoids extremes)
        if 35 <= f.rsi <= 65:
            score += 1.0

        # Tight Bollinger Band (ranging market)
        if f.bb_width < 0.03:
            score += 0.75

        # Volume present but not spiking (avoid breakout candles)
        if 0.8 <= f.volume_spike_ratio <= 1.5:
            score += 0.75

        confidence = score / total
        if confidence < 0.4:
            return None

        # Tighter stop for scalps
        sl_dist = f.atr * 1.0

        return StrategySignal(
            side=side,
            strategy_name=StrategyName.SCALPING.value,
            confidence=round(confidence, 4),
            entry_price=f.close,
            sl_distance=round(sl_dist, 8),
            features_used={
                "ema_fast": f.ema_fast,
                "ema_mid": f.ema_mid,
                "obv_slope": f.obv_slope,
                "rsi": f.rsi,
                "bb_width": f.bb_width,
                "volume_spike_ratio": f.volume_spike_ratio,
            },
        )

    # ── Strategy 5: Volatility Expansion ─────────────────────────

    def _volatility_expansion(
        self, f: FeatureSet, regime: RegimeResult
    ) -> StrategySignal | None:
        """
        Best for HIGH_VOLATILITY.

        Entry on ATR expansion + breakout candle + Supertrend flip.
        """
        score = 0.0
        total = 5
        side: str | None = None

        # ── ATR expansion ────────────────────────────────────────
        if f.atr_avg_20 > 0 and f.atr >= f.atr_avg_20 * 1.5:
            score += 1.0

        # ── Supertrend direction ─────────────────────────────────
        if f.supertrend_direction == 1:
            side = "BUY"
            score += 1.5
        elif f.supertrend_direction == -1:
            side = "SELL"
            score += 1.5
        else:
            return None

        # ── Breakout candle (large body relative to ATR) ─────────
        body = abs(f.close - f.open)
        if f.atr > 0 and body >= f.atr * 0.75:
            score += 1.0

        # ── MACD confirmation ────────────────────────────────────
        if side == "BUY" and f.macd_histogram > 0:
            score += 0.75
        elif side == "SELL" and f.macd_histogram < 0:
            score += 0.75

        # ── Volume spike ─────────────────────────────────────────
        if f.volume_spike_ratio >= 1.5:
            score += 0.75

        confidence = score / total
        if confidence < 0.4:
            return None

        # Wider stop for volatile markets
        sl_dist = f.atr * 2.0

        return StrategySignal(
            side=side,
            strategy_name=StrategyName.VOLATILITY_EXPANSION.value,
            confidence=round(confidence, 4),
            entry_price=f.close,
            sl_distance=round(sl_dist, 8),
            features_used={
                "atr": f.atr,
                "atr_avg_20": f.atr_avg_20,
                "supertrend_dir": f.supertrend_direction,
                "body_atr_ratio": round(body / f.atr, 4) if f.atr > 0 else 0.0,
                "macd_histogram": f.macd_histogram,
                "volume_spike_ratio": f.volume_spike_ratio,
            },
        )
