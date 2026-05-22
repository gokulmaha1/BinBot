"""
BinBot AI Auto Mode — Signal Scoring Engine
Weighted multi-factor scoring with pass/fail gate.
"""

import logging
from dataclasses import dataclass, field

from app.config import settings
from app.engine.features import FeatureSet
from app.engine.regime import MarketRegime, RegimeResult
from app.engine.strategies import StrategySignal

logger = logging.getLogger(__name__)


# ── Scoring weights ──────────────────────────────────────────────

WEIGHT_TREND: float = 0.30
WEIGHT_MOMENTUM: float = 0.20
WEIGHT_VOLUME: float = 0.15
WEIGHT_VOLATILITY: float = 0.15
WEIGHT_STRUCTURE: float = 0.10
WEIGHT_ORDER_FLOW: float = 0.10


# ── Result dataclass ─────────────────────────────────────────────

@dataclass
class ScoredSignal:
    """Final scored signal with breakdown and pass/fail gate."""

    signal: StrategySignal
    total_score: float                     # 0–100
    passed: bool                           # total_score >= threshold
    breakdown: dict[str, float] = field(default_factory=dict)  # factor → score (0–100 each)
    threshold: int = settings.SIGNAL_SCORE_THRESHOLD


class SignalScorer:
    """Computes a weighted 0-100 score for a strategy signal."""

    def score(
        self,
        signal: StrategySignal,
        features: FeatureSet,
        regime: RegimeResult,
    ) -> ScoredSignal:
        """
        Score a signal on six factors.

        Parameters
        ----------
        signal : StrategySignal
        features : FeatureSet
        regime : RegimeResult

        Returns
        -------
        ScoredSignal
        """
        trend_score = self._score_trend(signal, features, regime)
        momentum_score = self._score_momentum(signal, features)
        volume_score = self._score_volume(signal, features)
        volatility_score = self._score_volatility(features, regime)
        structure_score = self._score_structure(signal, features)
        order_flow_score = self._score_order_flow(signal, features)

        total = (
            trend_score * WEIGHT_TREND
            + momentum_score * WEIGHT_MOMENTUM
            + volume_score * WEIGHT_VOLUME
            + volatility_score * WEIGHT_VOLATILITY
            + structure_score * WEIGHT_STRUCTURE
            + order_flow_score * WEIGHT_ORDER_FLOW
        )
        total = round(min(max(total, 0.0), 100.0), 2)

        breakdown = {
            "trend_alignment": round(trend_score, 2),
            "momentum": round(momentum_score, 2),
            "volume": round(volume_score, 2),
            "volatility": round(volatility_score, 2),
            "market_structure": round(structure_score, 2),
            "order_flow": round(order_flow_score, 2),
        }

        passed = total >= settings.SIGNAL_SCORE_THRESHOLD

        scored = ScoredSignal(
            signal=signal,
            total_score=total,
            passed=passed,
            breakdown=breakdown,
        )

        logger.info(
            "Signal scored for %s [%s %s]: total=%.1f passed=%s | %s",
            features.symbol,
            signal.side,
            signal.strategy_name,
            total,
            passed,
            " | ".join(f"{k}={v:.0f}" for k, v in breakdown.items()),
        )
        return scored

    # ── Factor 1: Trend Alignment (30%) ──────────────────────────

    @staticmethod
    def _score_trend(
        signal: StrategySignal,
        f: FeatureSet,
        regime: RegimeResult,
    ) -> float:
        """
        Score 0-100 based on EMA stack, ADX strength, and Supertrend.
        """
        score = 0.0
        is_buy = signal.side == "BUY"

        # EMA stack alignment (up to 40 points)
        if is_buy:
            if f.ema_fast > f.ema_mid > f.ema_slow > f.ema_trend:
                score += 40
            elif f.ema_fast > f.ema_mid > f.ema_slow:
                score += 30
            elif f.ema_fast > f.ema_mid:
                score += 15
        else:
            if f.ema_fast < f.ema_mid < f.ema_slow < f.ema_trend:
                score += 40
            elif f.ema_fast < f.ema_mid < f.ema_slow:
                score += 30
            elif f.ema_fast < f.ema_mid:
                score += 15

        # ADX strength (up to 30 points)
        if f.adx >= 40:
            score += 30
        elif f.adx >= 25:
            score += 20
        elif f.adx >= 20:
            score += 10

        # Supertrend alignment (up to 30 points)
        if is_buy and f.supertrend_direction == 1:
            score += 30
        elif not is_buy and f.supertrend_direction == -1:
            score += 30
        # Partial credit if close is above/below supertrend
        elif is_buy and f.close > f.supertrend:
            score += 15
        elif not is_buy and f.close < f.supertrend:
            score += 15

        return min(score, 100.0)

    # ── Factor 2: Momentum (20%) ─────────────────────────────────

    @staticmethod
    def _score_momentum(signal: StrategySignal, f: FeatureSet) -> float:
        """
        Score 0-100 based on RSI, MACD, and Stochastic RSI alignment.
        """
        score = 0.0
        is_buy = signal.side == "BUY"

        # RSI (up to 35 points)
        if is_buy:
            if 40 <= f.rsi <= 65:
                score += 35  # ideal buy zone
            elif 30 <= f.rsi < 40:
                score += 25  # still okay
            elif f.rsi < 30:
                score += 15  # oversold — risky for trend, good for reversion
            elif 65 < f.rsi <= 75:
                score += 10  # strong momentum, but getting high
        else:
            if 35 <= f.rsi <= 60:
                score += 35
            elif 60 < f.rsi <= 70:
                score += 25
            elif f.rsi > 70:
                score += 15
            elif 25 <= f.rsi < 35:
                score += 10

        # MACD (up to 35 points)
        if is_buy:
            if f.macd_line > f.macd_signal and f.macd_histogram > 0:
                score += 35
            elif f.macd_line > f.macd_signal:
                score += 20
            elif f.macd_histogram > 0:
                score += 10
        else:
            if f.macd_line < f.macd_signal and f.macd_histogram < 0:
                score += 35
            elif f.macd_line < f.macd_signal:
                score += 20
            elif f.macd_histogram < 0:
                score += 10

        # Stochastic RSI (up to 30 points)
        if is_buy:
            if f.stoch_rsi_k > f.stoch_rsi_d and f.stoch_rsi_k < 80:
                score += 30
            elif f.stoch_rsi_k > f.stoch_rsi_d:
                score += 15
            elif f.stoch_rsi_k < 30:
                score += 20  # oversold bounce potential
        else:
            if f.stoch_rsi_k < f.stoch_rsi_d and f.stoch_rsi_k > 20:
                score += 30
            elif f.stoch_rsi_k < f.stoch_rsi_d:
                score += 15
            elif f.stoch_rsi_k > 70:
                score += 20

        return min(score, 100.0)

    # ── Factor 3: Volume (15%) ───────────────────────────────────

    @staticmethod
    def _score_volume(signal: StrategySignal, f: FeatureSet) -> float:
        """
        Score 0-100 based on OBV trend and volume spike ratio.
        """
        score = 0.0
        is_buy = signal.side == "BUY"

        # Volume spike ratio (up to 50 points)
        if f.volume_spike_ratio >= 2.0:
            score += 50
        elif f.volume_spike_ratio >= 1.5:
            score += 40
        elif f.volume_spike_ratio >= 1.2:
            score += 25
        elif f.volume_spike_ratio >= 1.0:
            score += 15
        else:
            score += 5  # below average volume

        # OBV direction alignment (up to 50 points)
        if is_buy and f.obv_slope > 0:
            score += 50
        elif not is_buy and f.obv_slope < 0:
            score += 50
        elif f.obv_slope == 0:
            score += 20
        else:
            score += 5  # OBV diverging from signal

        return min(score, 100.0)

    # ── Factor 4: Volatility (15%) ───────────────────────────────

    @staticmethod
    def _score_volatility(f: FeatureSet, regime: RegimeResult) -> float:
        """
        Score 0-100 based on ATR relative level and BB position.
        """
        score = 0.0

        # ATR relative to average (up to 50 points)
        # Moderate volatility is best for most strategies
        if f.atr_avg_20 > 0:
            ratio = f.atr / f.atr_avg_20
            if 0.8 <= ratio <= 1.5:
                score += 50  # normal — ideal
            elif 1.5 < ratio <= 2.0:
                score += 35  # elevated but tradeable
            elif 0.5 <= ratio < 0.8:
                score += 30  # low but okay
            elif ratio > 2.0:
                score += 15  # high risk
            else:
                score += 10  # very low
        else:
            score += 25  # can't determine, neutral

        # Bollinger Band position (up to 50 points)
        # Middle of bands = high score; edges depend on strategy
        pct_b = f.bb_percent_b
        if regime.regime in (
            MarketRegime.MEAN_REVERSION.value,
            MarketRegime.RANGING.value,
        ):
            # For mean reversion, edges are good
            if pct_b <= 0.1 or pct_b >= 0.9:
                score += 50
            elif pct_b <= 0.2 or pct_b >= 0.8:
                score += 35
            else:
                score += 15
        else:
            # For trend/breakout, middle-to-edge in signal direction is good
            if 0.3 <= pct_b <= 0.7:
                score += 40
            elif 0.2 <= pct_b <= 0.8:
                score += 30
            else:
                score += 20

        return min(score, 100.0)

    # ── Factor 5: Market Structure (10%) ─────────────────────────

    @staticmethod
    def _score_structure(signal: StrategySignal, f: FeatureSet) -> float:
        """
        Score 0-100 based on S/R proximity and break of structure.
        """
        score = 0.0
        is_buy = signal.side == "BUY"

        # S/R proximity (up to 50 points)
        if is_buy:
            # For buys: near support = good entry, far from resistance = room to run
            if f.nearest_support > 0:
                dist_to_support = abs(f.close - f.nearest_support)
                if f.atr > 0:
                    atr_dist = dist_to_support / f.atr
                    if atr_dist <= 1.0:
                        score += 50  # very close to support
                    elif atr_dist <= 2.0:
                        score += 35
                    else:
                        score += 15
                else:
                    score += 25
            else:
                score += 20

            # Break of resistance (up to 50 points)
            if f.nearest_resistance > 0 and f.close > f.nearest_resistance:
                score += 50
            elif f.nearest_resistance > 0:
                dist_to_res = (f.nearest_resistance - f.close)
                if f.atr > 0 and dist_to_res / f.atr > 2.0:
                    score += 35  # plenty of room
                else:
                    score += 15
            else:
                score += 25
        else:
            # For sells: near resistance = good entry
            if f.nearest_resistance > 0:
                dist_to_res = abs(f.close - f.nearest_resistance)
                if f.atr > 0:
                    atr_dist = dist_to_res / f.atr
                    if atr_dist <= 1.0:
                        score += 50
                    elif atr_dist <= 2.0:
                        score += 35
                    else:
                        score += 15
                else:
                    score += 25
            else:
                score += 20

            # Break of support
            if f.nearest_support > 0 and f.close < f.nearest_support:
                score += 50
            elif f.nearest_support > 0:
                dist_to_sup = (f.close - f.nearest_support)
                if f.atr > 0 and dist_to_sup / f.atr > 2.0:
                    score += 35
                else:
                    score += 15
            else:
                score += 25

        return min(score, 100.0)

    # ── Factor 6: Order Flow (10%) ───────────────────────────────

    @staticmethod
    def _score_order_flow(signal: StrategySignal, f: FeatureSet) -> float:
        """
        Score 0-100 based on buy/sell pressure proxies.

        Uses OBV slope, volume-price relationship, and candle body
        as proxy for order-book imbalance (real book data requires
        separate websocket feed).
        """
        score = 0.0
        is_buy = signal.side == "BUY"

        # OBV slope direction (up to 40 points)
        if is_buy and f.obv_slope > 0:
            score += 40
        elif not is_buy and f.obv_slope < 0:
            score += 40
        elif f.obv_slope == 0:
            score += 15
        else:
            score += 5

        # Candle body as buying/selling pressure proxy (up to 30 points)
        body = f.close - f.open
        wick_range = f.high - f.low
        if wick_range > 0:
            body_ratio = abs(body) / wick_range
        else:
            body_ratio = 0.0

        if is_buy and body > 0 and body_ratio > 0.6:
            score += 30  # strong bullish candle
        elif not is_buy and body < 0 and body_ratio > 0.6:
            score += 30  # strong bearish candle
        elif (is_buy and body > 0) or (not is_buy and body < 0):
            score += 15  # direction matches but weak body
        else:
            score += 5

        # Volume confirms direction (up to 30 points)
        if f.volume_spike_ratio >= 1.5:
            if (is_buy and body > 0) or (not is_buy and body < 0):
                score += 30  # high volume in signal direction
            else:
                score += 10  # high volume against signal
        elif f.volume_spike_ratio >= 1.0:
            score += 15
        else:
            score += 5

        return min(score, 100.0)
