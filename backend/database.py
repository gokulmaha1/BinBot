from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import pytz
import os
from dotenv import load_dotenv

load_dotenv()

IST = pytz.timezone('Asia/Kolkata')

def get_ist_now():
    return datetime.datetime.now(IST)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./database/trading_bot.db")

Base = declarative_base()

# Optimized for high-frequency writes and sub-second pulses
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False, "timeout": 30}
)

# Enable WAL Mode (Write-Ahead Logging) to prevent Disk I/O errors
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)
    side = Column(String)
    leverage = Column(Integer, default=5)
    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float)
    fee = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0) # Net PnL after fees
    status = Column(String) # OPEN, CLOSED
    entry_time = Column(DateTime, default=get_ist_now)
    exit_time = Column(DateTime, nullable=True)

class LogEntry(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=get_ist_now)
    level = Column(String)
    message = Column(String)

class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True)
    leverage = Column(Integer, default=20)
    take_profit = Column(Float, default=0.01)
    stop_loss = Column(Float, default=0.015)
    daily_loss_limit = Column(Float, default=200.0)
    use_dynamic = Column(Boolean, default=True)
    dynamic_risk_pct = Column(Float, default=0.50)
    dca_enabled = Column(Boolean, default=True)
    trailing_sl_enabled = Column(Boolean, default=True)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Seed default config if empty
    db = SessionLocal()
    if not db.query(Config).first():
        db.add(Config())
        db.commit()
    db.close()
