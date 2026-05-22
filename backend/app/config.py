"""
BinBot AI Auto Mode — Application Configuration
Pydantic Settings driven by environment variables.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from enum import Enum


class TradingMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class Settings(BaseSettings):
    """Central configuration loaded from .env file."""

    # ── Application ──────────────────────────────────────────────
    APP_NAME: str = "BinBot AI Auto Mode"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # ── Database ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://binbot:binbot@localhost:5432/binbot"
    DATABASE_ECHO: bool = False

    # ── Redis ────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Security ─────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ENCRYPTION_KEY: str = "change-me-32-byte-key-for-aes256"  # Must be 32 bytes

    # ── Authentication ───────────────────────────────────────────
    DASHBOARD_USER: str = "admin"
    DASHBOARD_PASS: str = "admin"

    # ── Binance API ──────────────────────────────────────────────
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET_API_KEY: str = ""
    BINANCE_TESTNET_API_SECRET: str = ""

    # ── Trading Mode ─────────────────────────────────────────────
    TRADING_MODE: TradingMode = TradingMode.PAPER

    # ── Notifications ────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # ── Risk Management (HARDCODED LIMITS — NOT OVERRIDABLE) ────
    MAX_RISK_PER_TRADE: float = 0.01         # 1% of equity
    MAX_DAILY_LOSS: float = 0.03             # 3% of starting daily equity
    MAX_DRAWDOWN: float = 0.08               # 8% from equity peak
    MAX_CONSECUTIVE_LOSSES: int = 3          # Pause after 3 consecutive losses
    CONSECUTIVE_LOSS_COOLDOWN: int = 1800    # 30 minutes cooldown (seconds)
    MAX_ACTIVE_POSITIONS: int = 3
    MAX_CORRELATED_POSITIONS: int = 1
    CORRELATION_THRESHOLD: float = 0.85
    MAX_LEVERAGE: int = 20
    MAX_TRADES_PER_DAY: int = 10
    CAPITAL_PER_TRADE_PCT: float = 0.20      # 20% of wallet per trade

    # ── Signal Thresholds ────────────────────────────────────────
    SIGNAL_SCORE_THRESHOLD: int = 80         # Minimum signal score (0-100)
    ML_CONFIDENCE_THRESHOLD: float = 0.75    # Minimum ML confirmation (0-1)

    # ── Scanner Settings ─────────────────────────────────────────
    SCANNER_INTERVAL_SECONDS: int = 15
    SCANNER_MIN_VOLUME_24H: float = 50_000_000.0   # $50M minimum
    SCANNER_MAX_SPREAD_PCT: float = 0.001           # 0.1%
    SCANNER_MIN_LISTING_DAYS: int = 30
    SCANNER_TOP_PAIRS: int = 20
    SCANNER_MANUAL_PAIRS: str = ""

    # ── Take Profit Tiers ────────────────────────────────────────
    TP1_RATIO: float = 1.0    # 1:1 R:R
    TP1_CLOSE_PCT: float = 0.40   # Close 40%
    TP2_RATIO: float = 2.0    # 1:2 R:R
    TP2_CLOSE_PCT: float = 0.30   # Close 30%
    TP3_RATIO: float = 3.0    # 1:3 R:R
    TP3_CLOSE_PCT: float = 0.30   # Close remaining 30%

    # ── Technical Indicators ─────────────────────────────────────
    EMA_FAST: int = 9
    EMA_MID: int = 21
    EMA_SLOW: int = 50
    EMA_TREND: int = 200
    RSI_PERIOD: int = 14
    ATR_PERIOD: int = 14
    BB_PERIOD: int = 20
    BB_STD: float = 2.0
    ADX_PERIOD: int = 14
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    SUPERTREND_PERIOD: int = 10
    SUPERTREND_MULTIPLIER: float = 3.0

    # ── Data Engine ──────────────────────────────────────────────
    WS_RECONNECT_MAX_DELAY: int = 60         # Max reconnect delay (seconds)
    WS_PING_INTERVAL: int = 30               # Ping every 30s
    WS_PING_TIMEOUT: int = 10                # Reconnect if no pong in 10s
    CANDLE_BUFFER_SIZE: int = 500             # Rolling window size
    TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h", "4h"]

    @property
    def is_live(self) -> bool:
        return self.TRADING_MODE == TradingMode.LIVE

    @property
    def is_testnet(self) -> bool:
        return self.TRADING_MODE == TradingMode.TESTNET

    @property
    def is_paper(self) -> bool:
        return self.TRADING_MODE == TradingMode.PAPER

    @property
    def active_api_key(self) -> str:
        if self.is_testnet:
            return self.BINANCE_TESTNET_API_KEY
        return self.BINANCE_API_KEY

    @property
    def active_api_secret(self) -> str:
        if self.is_testnet:
            return self.BINANCE_TESTNET_API_SECRET
        return self.BINANCE_API_SECRET

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
