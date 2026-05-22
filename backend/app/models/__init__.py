"""
BinBot AI Auto Mode — Database Models
All SQLAlchemy ORM models for the trading platform.
"""

import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Float, Integer, BigInteger, Boolean,
    DateTime, Date, Text, LargeBinary, JSON, ForeignKey,
    UniqueConstraint, Index, Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.session import Base


# ── Enums ────────────────────────────────────────────────────────

import enum

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"

class ExchangeMode(str, enum.Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"

class BotStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"

class StrategyType(str, enum.Enum):
    TREND = "trend"
    BREAKOUT = "breakout"
    REVERSION = "reversion"
    SCALP = "scalp"
    VOLATILITY = "volatility"

class SignalSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"

class SignalStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"

class TradeStatus(str, enum.Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL_TP = "partial_tp"
    CLOSED = "closed"

class TradeState(str, enum.Enum):
    ENTRY = "entry"
    TP1_HIT = "tp1_hit"
    BE_MOVED = "be_moved"
    TP2_HIT = "tp2_hit"
    TP3_HIT = "tp3_hit"
    SL_HIT = "sl_hit"
    MANUAL = "manual"

class LogLevel(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    TRADE = "trade"

class LogSource(str, enum.Enum):
    SCANNER = "scanner"
    STRATEGY = "strategy"
    RISK = "risk"
    EXECUTOR = "executor"
    ML = "ml"
    MONITOR = "monitor"
    SYSTEM = "system"
    DATA = "data"


# ── Models ───────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    exchange_accounts = relationship("ExchangeAccount", back_populates="user", cascade="all, delete-orphan")
    bots = relationship("Bot", back_populates="user", cascade="all, delete-orphan")


class ExchangeAccount(Base):
    __tablename__ = "exchange_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exchange = Column(String(50), default="binance", nullable=False)
    api_key_encrypted = Column(LargeBinary, nullable=False)
    api_secret_encrypted = Column(LargeBinary, nullable=False)
    mode = Column(SAEnum(ExchangeMode), default=ExchangeMode.PAPER, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="exchange_accounts")
    bots = relationship("Bot", back_populates="exchange_account")


class Bot(Base):
    __tablename__ = "bots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exchange_account_id = Column(UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(100), default="AI Auto Bot", nullable=False)
    status = Column(SAEnum(BotStatus), default=BotStatus.IDLE, nullable=False)
    risk_config = Column(JSON, default=dict, nullable=False)
    daily_pnl = Column(Float, default=0.0, nullable=False)
    daily_starting_equity = Column(Float, default=0.0, nullable=False)
    peak_equity = Column(Float, default=0.0, nullable=False)
    consecutive_losses = Column(Integer, default=0, nullable=False)
    cooldown_until = Column(DateTime, nullable=True)
    trades_today = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    last_reset_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="bots")
    exchange_account = relationship("ExchangeAccount", back_populates="bots")
    trades = relationship("Trade", back_populates="bot", cascade="all, delete-orphan")
    signals = relationship("Signal", back_populates="bot", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="bot", cascade="all, delete-orphan")
    performance_metrics = relationship("PerformanceMetric", back_populates="bot", cascade="all, delete-orphan")


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)
    type = Column(SAEnum(StrategyType), nullable=False)
    parameters = Column(JSON, default=dict, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    total_trades = Column(Integer, default=0, nullable=False)
    winning_trades = Column(Integer, default=0, nullable=False)
    win_rate = Column(Float, default=0.0, nullable=False)
    avg_pnl = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Signal(Base):
    __tablename__ = "signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id = Column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(SAEnum(SignalSide), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    score = Column(Float, default=0.0, nullable=False)
    ml_confidence = Column(Float, default=0.0, nullable=False)
    score_breakdown = Column(JSON, default=dict, nullable=False)
    features_snapshot = Column(JSON, default=dict, nullable=False)
    regime = Column(String(50), nullable=True)
    status = Column(SAEnum(SignalStatus), default=SignalStatus.ACCEPTED, nullable=False)
    reject_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    bot = relationship("Bot", back_populates="signals")
    trade = relationship("Trade", back_populates="signal", uselist=False)

    # Indexes
    __table_args__ = (
        Index("ix_signals_created_at", "created_at"),
        Index("ix_signals_bot_status", "bot_id", "status"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id = Column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False)
    signal_id = Column(UUID(as_uuid=True), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(SAEnum(SignalSide), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    leverage = Column(Integer, default=1, nullable=False)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    remaining_quantity = Column(Float, nullable=False)  # Tracks partial closes
    sl_price = Column(Float, nullable=False)
    tp1_price = Column(Float, nullable=True)
    tp2_price = Column(Float, nullable=True)
    tp3_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, default=0.0, nullable=False)
    fees = Column(Float, default=0.0, nullable=False)
    slippage = Column(Float, default=0.0, nullable=False)
    status = Column(SAEnum(TradeStatus), default=TradeStatus.PENDING, nullable=False)
    trade_state = Column(SAEnum(TradeState), default=TradeState.ENTRY, nullable=False)
    close_reason = Column(String(50), nullable=True)
    binance_order_id = Column(String(50), nullable=True)
    entry_time = Column(DateTime, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    bot = relationship("Bot", back_populates="trades")
    signal = relationship("Signal", back_populates="trade")
    position = relationship("Position", back_populates="trade", uselist=False, cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("ix_trades_status", "status"),
        Index("ix_trades_bot_status", "bot_id", "status"),
        Index("ix_trades_entry_time", "entry_time"),
    )


class Position(Base):
    __tablename__ = "positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id = Column(UUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, unique=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(SAEnum(SignalSide), nullable=False)
    quantity = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, default=0.0, nullable=False)
    mark_price = Column(Float, default=0.0, nullable=False)
    liquidation_price = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    trade = relationship("Trade", back_populates="position")


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bot_id = Column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    starting_equity = Column(Float, default=0.0, nullable=False)
    ending_equity = Column(Float, default=0.0, nullable=False)
    daily_pnl = Column(Float, default=0.0, nullable=False)
    daily_pnl_pct = Column(Float, default=0.0, nullable=False)
    max_drawdown = Column(Float, default=0.0, nullable=False)
    total_trades = Column(Integer, default=0, nullable=False)
    winning_trades = Column(Integer, default=0, nullable=False)
    win_rate = Column(Float, default=0.0, nullable=False)
    sharpe_ratio = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    bot = relationship("Bot", back_populates="performance_metrics")

    __table_args__ = (
        UniqueConstraint("bot_id", "date", name="uq_bot_date"),
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(20), nullable=False, index=True)
    price = Column(Float, nullable=False)
    volume_24h = Column(Float, nullable=False)
    open_interest = Column(Float, nullable=True)
    atr = Column(Float, nullable=True)
    adx = Column(Float, nullable=True)
    regime = Column(String(50), nullable=True)
    scanner_score = Column(Float, default=0.0, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_snapshots_captured", "captured_at"),
        Index("ix_snapshots_symbol_time", "symbol", "captured_at"),
    )


class Log(Base):
    __tablename__ = "logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=True)
    level = Column(SAEnum(LogLevel), default=LogLevel.INFO, nullable=False)
    source = Column(SAEnum(LogSource), default=LogSource.SYSTEM, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    bot = relationship("Bot", back_populates="logs")
