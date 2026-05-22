"""
BinBot AI Auto Mode — ML Confirmation Engine
XGBoost-based trade confirmation with rule-based fallback.
ML never trades independently — it only confirms or rejects signals.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from app.config import settings

logger = logging.getLogger(__name__)

# ── ML Model Path ────────────────────────────────────────────────
MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "ml_models"
MODEL_PATH = MODEL_DIR / "ml_model.json"

# ── Feature columns expected by the model ────────────────────────
FEATURE_COLUMNS: list[str] = [
    "rsi",
    "ema_fast_dist",
    "ema_mid_dist",
    "ema_slow_dist",
    "ema_trend_dist",
    "macd_hist",
    "macd_hist_slope",
    "adx",
    "bb_position",
    "atr_pct",
    "volume_sma_ratio",
    "volume_spike",
    "oi_change_pct",
    "funding_rate",
    "ob_imbalance",
    "wick_rejection_ratio",
    "body_ratio",
    "consecutive_candle_dir",
    "higher_tf_alignment",
    "regime_score",
]


@dataclass(frozen=True)
class MLResult:
    """Result returned by the ML confirmation engine."""

    probability: float
    feature_importances: dict[str, float]
    is_fallback: bool
    regime: str = "unknown"
    risk_level: str = "medium"
    meets_threshold: bool = False


class MLConfirmationEngine:
    """
    XGBoost-based confirmation engine.

    - Loads a trained model from disk if available.
    - Falls back to a rule-based scoring system ported from
      ai_engine/model.py when no trained model exists.
    - ML never trades independently; it only confirms/rejects.
    """

    def __init__(self) -> None:
        """Load the XGBoost model from disk if it exists."""
        self.model: Optional[xgb.Booster] = None
        self._load_model()

    # ── Public API ───────────────────────────────────────────────

    async def predict(self, features: dict) -> MLResult:
        """
        Run XGBoost prediction on the given feature dict.

        Falls back to rule-based scoring when no trained model is
        available.  Returns an MLResult with probability, feature
        importances, and whether the fallback was used.
        """
        try:
            if self.model is not None:
                return self._predict_xgboost(features)
            return self._predict_rule_based(features)
        except Exception as exc:
            logger.error("ML prediction failed, using rule-based fallback: %s", exc, exc_info=True)
            return self._predict_rule_based(features)

    async def train(self, trades_df: pd.DataFrame) -> bool:
        """
        Train XGBoost on historical trade outcomes and save model.

        Expected columns in trades_df:
            - All FEATURE_COLUMNS (or a subset; missing ones are filled
              with 0)
            - 'outcome' — 1 for winning trade, 0 for losing trade

        Returns True on success, False on failure.
        """
        try:
            if trades_df.empty or len(trades_df) < 50:
                logger.warning("Not enough trades to train (%d). Need >= 50.", len(trades_df))
                return False

            # Prepare features & labels
            X = self._prepare_training_features(trades_df)
            y = trades_df["outcome"].astype(int).values

            dtrain = xgb.DMatrix(X, label=y, feature_names=list(X.columns))

            params = {
                "objective": "binary:logistic",
                "eval_metric": "logloss",
                "max_depth": 6,
                "learning_rate": 0.05,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "min_child_weight": 3,
                "gamma": 0.1,
                "reg_alpha": 0.1,
                "reg_lambda": 1.0,
                "seed": 42,
            }

            self.model = xgb.train(
                params,
                dtrain,
                num_boost_round=200,
                early_stopping_rounds=20,
                evals=[(dtrain, "train")],
                verbose_eval=False,
            )

            # Persist
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            self.model.save_model(str(MODEL_PATH))
            logger.info("ML model trained on %d trades and saved to %s", len(trades_df), MODEL_PATH)
            return True

        except Exception as exc:
            logger.error("ML training failed: %s", exc, exc_info=True)
            return False

    def reload_model(self) -> bool:
        """Hot-reload the model from disk (e.g. after retraining)."""
        return self._load_model()

    # ── XGBoost prediction ───────────────────────────────────────

    def _predict_xgboost(self, features: dict) -> MLResult:
        """Run inference through the loaded XGBoost model."""
        feature_row = self._prepare_inference_features(features)
        dmatrix = xgb.DMatrix(feature_row, feature_names=list(feature_row.columns))

        probability = float(self.model.predict(dmatrix)[0])

        # Feature importances
        raw_importance = self.model.get_score(importance_type="gain")
        total = sum(raw_importance.values()) or 1.0
        importances = {k: round(v / total, 4) for k, v in raw_importance.items()}

        regime = self._classify_regime(features)
        risk_level = self._classify_risk(probability)

        result = MLResult(
            probability=round(probability, 4),
            feature_importances=importances,
            is_fallback=False,
            regime=regime,
            risk_level=risk_level,
            meets_threshold=probability >= settings.ML_CONFIDENCE_THRESHOLD,
        )

        logger.info(
            "ML prediction: prob=%.4f threshold=%.2f meets=%s regime=%s",
            result.probability,
            settings.ML_CONFIDENCE_THRESHOLD,
            result.meets_threshold,
            result.regime,
        )
        return result

    # ── Rule-based fallback (ported from ai_engine/model.py) ─────

    def _predict_rule_based(self, features: dict) -> MLResult:
        """
        Rule-based trade quality scoring.

        Ported from ai_engine/model.py::get_trade_quality_score with
        expanded feature set.
        """
        score = 0.5  # Baseline
        importances: dict[str, float] = {}

        # 1. RSI Strength (Mean Reversion Check)
        rsi = features.get("rsi", 50.0)
        if rsi < 30 or rsi > 70:
            score += 0.10
            importances["rsi"] = 0.10
        elif rsi < 35 or rsi > 65:
            score += 0.05
            importances["rsi"] = 0.05

        # 2. EMA Spread (Trend Strength)
        ema_spread = features.get("ema_fast_dist", 0.0)
        if abs(ema_spread) > 0.005:
            score += 0.10
            importances["ema_fast_dist"] = 0.10
        elif abs(ema_spread) > 0.003:
            score += 0.05
            importances["ema_fast_dist"] = 0.05

        # 3. Volume Spike (Whale Confirmation)
        vol_spike = features.get("volume_spike", 1.0)
        if vol_spike > 3.0:
            score += 0.15
            importances["volume_spike"] = 0.15
        elif vol_spike > 2.0:
            score += 0.10
            importances["volume_spike"] = 0.10

        # 4. Order Book Imbalance (Buy/Sell Wall Pressure)
        ob_imbalance = features.get("ob_imbalance", 0.5)
        if abs(ob_imbalance - 0.5) > 0.25:
            score += 0.10
            importances["ob_imbalance"] = 0.10
        elif abs(ob_imbalance - 0.5) > 0.15:
            score += 0.05
            importances["ob_imbalance"] = 0.05

        # 5. Open Interest Change (Trend Commitment)
        oi_change = features.get("oi_change_pct", 0.0)
        if oi_change > 0.02:
            score += 0.05
            importances["oi_change_pct"] = 0.05

        # 6. Funding Rate (Over-Leverage Check) — penalize extremes
        funding = features.get("funding_rate", 0.0001)
        if abs(funding) > 0.01:
            score -= 0.10
            importances["funding_rate"] = -0.10
        elif abs(funding) > 0.005:
            score -= 0.05
            importances["funding_rate"] = -0.05

        # 7. ADX (Trend Commitment)
        adx = features.get("adx", 20.0)
        if adx > 30:
            score += 0.08
            importances["adx"] = 0.08
        elif adx > 25:
            score += 0.04
            importances["adx"] = 0.04

        # 8. MACD Histogram (Momentum)
        macd_hist = features.get("macd_hist", 0.0)
        if abs(macd_hist) > 0:
            score += 0.05
            importances["macd_hist"] = 0.05

        # 9. Wick Rejection (Candle Structure)
        wick_ratio = features.get("wick_rejection_ratio", 0.0)
        if wick_ratio > 0.6:
            score += 0.10
            importances["wick_rejection_ratio"] = 0.10

        # 10. Higher Timeframe Alignment
        htf_align = features.get("higher_tf_alignment", 0.0)
        if htf_align > 0.5:
            score += 0.07
            importances["higher_tf_alignment"] = 0.07

        # Clamp to [0.10, 0.98]
        probability = round(min(0.98, max(0.10, score)), 4)
        regime = self._classify_regime(features)
        risk_level = self._classify_risk(probability)

        result = MLResult(
            probability=probability,
            feature_importances=importances,
            is_fallback=True,
            regime=regime,
            risk_level=risk_level,
            meets_threshold=probability >= settings.ML_CONFIDENCE_THRESHOLD,
        )

        logger.info(
            "ML fallback: prob=%.4f threshold=%.2f meets=%s regime=%s",
            result.probability,
            settings.ML_CONFIDENCE_THRESHOLD,
            result.meets_threshold,
            result.regime,
        )
        return result

    # ── Internal Helpers ─────────────────────────────────────────

    def _load_model(self) -> bool:
        """Attempt to load model from MODEL_PATH."""
        if MODEL_PATH.exists():
            try:
                self.model = xgb.Booster()
                self.model.load_model(str(MODEL_PATH))
                logger.info("ML model loaded from %s", MODEL_PATH)
                return True
            except Exception as exc:
                logger.error("Failed to load ML model: %s", exc)
                self.model = None
        else:
            logger.info("No ML model found at %s — using rule-based fallback.", MODEL_PATH)
        return False

    def _prepare_inference_features(self, features: dict) -> pd.DataFrame:
        """Build a single-row DataFrame from a feature dict, filling missing cols with 0."""
        row = {col: features.get(col, 0.0) for col in FEATURE_COLUMNS}
        return pd.DataFrame([row])

    def _prepare_training_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract / fill feature columns from a training DataFrame."""
        for col in FEATURE_COLUMNS:
            if col not in df.columns:
                df[col] = 0.0
        return df[FEATURE_COLUMNS].fillna(0.0)

    @staticmethod
    def _classify_regime(features: dict) -> str:
        """Classify market regime from feature snapshot."""
        adx = features.get("adx", 20.0)
        ema_dist = abs(features.get("ema_fast_dist", 0.0))
        regime_score = features.get("regime_score", 0.0)

        if regime_score > 0.7:
            return "strong_trend"
        if adx > 30 and ema_dist > 0.003:
            return "trend"
        if adx < 20:
            return "ranging"
        return "transitional"

    @staticmethod
    def _classify_risk(probability: float) -> str:
        """Map probability to a risk label."""
        if probability > 0.85:
            return "low"
        if probability > 0.65:
            return "medium"
        return "high"
