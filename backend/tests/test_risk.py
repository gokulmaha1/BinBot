import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timedelta

from app.engine.risk import RiskManager, RiskCheckResult, PositionSize, TPLevels
from app.models import Bot, Trade, TradeStatus, SignalSide, TradeState
from app.config import settings

@pytest.fixture
def mock_db():
    return AsyncMock()

@pytest.fixture
def risk_manager(mock_db):
    return RiskManager(db_session=mock_db, redis=None)

def test_calculate_position_size(risk_manager):
    # Test typical scenario
    equity = 10000.0
    entry_price = 100.0
    sl_distance = 2.0  # 2% stop distance
    
    # 1% risk per trade = $100
    # Qty = $100 / 2 = 50
    # Notional value = 50 * 100 = 5000
    # Capital allocation limit: 20% of 10000 = 2000
    # Required leverage: 5000 / 2000 = 2.5 -> rounded up to 3x
    # 3x is below MAX_LEVERAGE (5x), so leverage = 3, qty = 50
    
    pos_size = risk_manager.calculate_position_size(
        equity=equity,
        entry_price=entry_price,
        sl_distance=sl_distance,
        symbol="BTCUSDT",
        qty_precision=2
    )
    
    assert pos_size.quantity == 50.0
    assert pos_size.leverage == 3
    assert pos_size.risk_amount == 100.0
    assert pos_size.risk_pct == 0.01

def test_calculate_position_size_leverage_cap(risk_manager):
    # Test leverage cap reduction
    equity = 10000.0
    entry_price = 100.0
    sl_distance = 0.5  # tight stop: 0.5% stop distance
    
    # 1% risk = $100
    # Raw Qty = 100 / 0.5 = 200
    # Notional value = 200 * 100 = 20000
    # Capital allocation limit = 2000
    # Max leverage = 5x
    # Max notional allowed = 2000 * 5 = 10000
    # So Qty is capped at 10000 / 100 = 100
    
    pos_size = risk_manager.calculate_position_size(
        equity=equity,
        entry_price=entry_price,
        sl_distance=sl_distance,
        symbol="BTCUSDT",
        qty_precision=2
    )
    
    assert pos_size.quantity == 100.0
    assert pos_size.leverage == 5
    assert pos_size.notional_value == 10000.0
    assert pos_size.risk_pct == 0.005  # risk reduced to 0.5%

def test_calculate_tp_levels(risk_manager):
    tp_levels = risk_manager.calculate_tp_levels(
        entry_price=100.0,
        sl_distance=2.0,
        side="BUY",
        total_quantity=10.0,
        price_precision=2,
        qty_precision=2
    )
    
    # TP1: 1:1 R:R = 102.0, close 40% = 4.0 qty
    # TP2: 1:2 R:R = 104.0, close 30% = 3.0 qty
    # TP3: 1:3 R:R = 106.0, close 30% = 3.0 qty
    
    assert tp_levels.tp1.price == 102.0
    assert tp_levels.tp1.quantity == 4.0
    assert tp_levels.tp2.price == 104.0
    assert tp_levels.tp2.quantity == 3.0
    assert tp_levels.tp3.price == 106.0
    assert tp_levels.tp3.quantity == 3.0

@pytest.mark.asyncio
async def test_check_trade_allowed_max_positions(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=0.0,
        peak_equity=10000.0,
        consecutive_losses=0,
        cooldown_until=None,
        trades_today=0,
        last_reset_date=datetime.utcnow().date()
    )
    
    # Mock bot retrieval
    risk_manager._get_bot = AsyncMock(return_value=bot)
    
    # Mock active positions count to 3 (which is settings.MAX_ACTIVE_POSITIONS)
    risk_manager._count_active_positions = AsyncMock(return_value=3)
    
    res = await risk_manager.check_trade_allowed(bot_id, "BTCUSDT", "BUY", {})
    assert res.allowed is False
    assert "Max active positions reached" in res.reason

@pytest.mark.asyncio
async def test_check_trade_allowed_daily_loss_limit(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=-350.0,  # 3.5% loss (limit is 3%)
        peak_equity=10000.0,
        consecutive_losses=0,
        cooldown_until=None,
        trades_today=1,
        last_reset_date=datetime.utcnow().date()
    )
    
    risk_manager._get_bot = AsyncMock(return_value=bot)
    risk_manager._count_active_positions = AsyncMock(return_value=1)
    
    res = await risk_manager.check_trade_allowed(bot_id, "BTCUSDT", "BUY", {})
    assert res.allowed is False
    assert "Daily loss limit hit" in res.reason

@pytest.mark.asyncio
async def test_check_trade_allowed_cooldown(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=0.0,
        peak_equity=10000.0,
        consecutive_losses=3,
        cooldown_until=datetime.utcnow() + timedelta(minutes=15),
        trades_today=3,
        last_reset_date=datetime.utcnow().date()
    )
    
    risk_manager._get_bot = AsyncMock(return_value=bot)
    risk_manager._count_active_positions = AsyncMock(return_value=0)
    
    res = await risk_manager.check_trade_allowed(bot_id, "BTCUSDT", "BUY", {})
    assert res.allowed is False
    assert "Cooldown active" in res.reason

@pytest.mark.asyncio
async def test_check_trade_allowed_revenge_trading(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=0.0,
        peak_equity=10000.0,
        consecutive_losses=0,
        cooldown_until=None,
        trades_today=2,
        last_reset_date=datetime.utcnow().date()
    )
    
    with patch.object(risk_manager, '_get_bot', return_value=bot):
        risk_manager._count_active_positions = AsyncMock(return_value=1)
        risk_manager._get_open_symbols = AsyncMock(return_value=["ETHUSDT"])
        risk_manager.check_correlation = AsyncMock(return_value={"ETHUSDT": 0.3})
        risk_manager._check_revenge_trade = AsyncMock(return_value=True)  # revenge trade!
        risk_manager._has_open_position = AsyncMock(return_value=False)
        
        res = await risk_manager.check_trade_allowed(bot_id, "BTCUSDT", "BUY", {})
        assert res.allowed is False
        assert "Revenge trade blocked" in res.reason

@pytest.mark.asyncio
async def test_check_trade_allowed_averaging_down(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=0.0,
        peak_equity=10000.0,
        consecutive_losses=0,
        cooldown_until=None,
        trades_today=1,
        last_reset_date=datetime.utcnow().date()
    )
    
    with patch.object(risk_manager, '_get_bot', return_value=bot):
        risk_manager._count_active_positions = AsyncMock(return_value=1)
        risk_manager._get_open_symbols = AsyncMock(return_value=["BTCUSDT"])
        risk_manager.check_correlation = AsyncMock(return_value={})
        risk_manager._check_revenge_trade = AsyncMock(return_value=False)
        risk_manager._has_open_position = AsyncMock(return_value=True)  # already has open position!
        
        res = await risk_manager.check_trade_allowed(bot_id, "BTCUSDT", "BUY", {})
        assert res.allowed is False
        assert "Averaging down blocked" in res.reason

@pytest.mark.asyncio
async def test_update_daily_stats(risk_manager, mock_db):
    bot_id = uuid4()
    bot = Bot(
        id=bot_id,
        daily_starting_equity=10000.0,
        daily_pnl=0.0,
        peak_equity=10000.0,
        consecutive_losses=0,
        cooldown_until=None,
        trades_today=0,
        last_reset_date=datetime.utcnow().date()
    )
    
    with patch.object(risk_manager, '_get_bot', return_value=bot):
        # Loss trade
        await risk_manager.update_daily_stats(bot_id, -100.0)
        assert bot.daily_pnl == -100.0
        assert bot.trades_today == 1
        assert bot.consecutive_losses == 1
        
        # Profit trade
        await risk_manager.update_daily_stats(bot_id, 250.0)
        assert bot.daily_pnl == 150.0
        assert bot.trades_today == 2
        assert bot.consecutive_losses == 0
        assert bot.peak_equity == 10150.0
