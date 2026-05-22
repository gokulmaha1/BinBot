import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.api.analytics import get_overview
from app.config import settings, TradingMode
from app.models import Bot, BotStatus

@pytest.mark.asyncio
async def test_get_overview_paper():
    # Mock DB session
    mock_db = AsyncMock()
    
    # Mock bot object
    bot_id = uuid4()
    mock_bot = Bot(
        id=bot_id,
        user_id=uuid4(),
        peak_equity=12345.67,
        daily_pnl=50.0,
        status=BotStatus.RUNNING,
    )
    
    # Mock db.execute calls
    mock_bot_scalar = MagicMock()
    mock_bot_scalar.scalar_one_or_none.return_value = mock_bot
    
    mock_scalar_results = MagicMock()
    mock_scalar_results.scalar.return_value = 100.0
    
    mock_db.execute.side_effect = [
        mock_bot_scalar,     # Bot select
        mock_scalar_results,  # total PnL
        mock_scalar_results,  # monthly PnL
        mock_scalar_results,  # total trades count
        mock_scalar_results,  # win count
        mock_scalar_results,  # active trades count
        mock_scalar_results,  # gross profit
        mock_scalar_results,  # gross loss
    ]
    
    # Ensure paper mode is set
    original_mode = settings.TRADING_MODE
    settings.TRADING_MODE = TradingMode.PAPER
    
    try:
        current_user = {"user_id": str(mock_bot.user_id)}
        res = await get_overview(db=mock_db, current_user=current_user)
        
        assert res.balance == 12345.67
        assert res.trading_mode == "paper"
    finally:
        settings.TRADING_MODE = original_mode


@pytest.mark.asyncio
@patch("app.engine.executor.TradeExecutor.get_balance")
async def test_get_overview_live(mock_get_balance):
    # Mock dynamic balance return
    mock_get_balance.return_value = 98765.43
    
    # Mock DB session
    mock_db = AsyncMock()
    
    # Mock bot object
    bot_id = uuid4()
    mock_bot = Bot(
        id=bot_id,
        user_id=uuid4(),
        peak_equity=12345.67,
        daily_pnl=50.0,
        status=BotStatus.RUNNING,
    )
    
    # Mock db.execute calls
    mock_bot_scalar = MagicMock()
    mock_bot_scalar.scalar_one_or_none.return_value = mock_bot
    
    mock_scalar_results = MagicMock()
    mock_scalar_results.scalar.return_value = 100.0
    
    mock_db.execute.side_effect = [
        mock_bot_scalar,
        mock_scalar_results,
        mock_scalar_results,
        mock_scalar_results,
        mock_scalar_results,
        mock_scalar_results,
        mock_scalar_results,
        mock_scalar_results,
    ]
    
    # Ensure live mode is set
    original_mode = settings.TRADING_MODE
    settings.TRADING_MODE = TradingMode.LIVE
    
    try:
        # Patch the close_connection so it doesn't try to connect to a real websockets client
        with patch("app.engine.executor.TradeExecutor._ensure_client", AsyncMock()), \
             patch("binance.AsyncClient.close_connection", AsyncMock()):
            current_user = {"user_id": str(mock_bot.user_id)}
            res = await get_overview(db=mock_db, current_user=current_user)
            
            assert res.balance == 98765.43
            assert res.trading_mode == "live"
    finally:
        settings.TRADING_MODE = original_mode
