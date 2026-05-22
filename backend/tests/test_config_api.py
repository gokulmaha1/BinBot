import pytest
from fastapi import HTTPException
from app.api.config import update_config, UpdateConfigRequest
from app.config import settings, TradingMode

@pytest.mark.asyncio
async def test_update_config_trading_mode():
    # Store original trading mode to avoid side-effects on other tests
    original_mode = settings.TRADING_MODE

    try:
        # 1. Update to LIVE mode
        req = UpdateConfigRequest(trading_mode="live")
        res = await update_config(req=req, current_user={"user_id": "12345678-1234-5678-1234-567812345678"})
        assert res["success"] is True
        assert res["updated"]["trading_mode"] == "live"
        assert settings.TRADING_MODE == TradingMode.LIVE

        # 2. Update to TESTNET mode
        req = UpdateConfigRequest(trading_mode="testnet")
        res = await update_config(req=req, current_user={"user_id": "12345678-1234-5678-1234-567812345678"})
        assert res["success"] is True
        assert res["updated"]["trading_mode"] == "testnet"
        assert settings.TRADING_MODE == TradingMode.TESTNET

        # 3. Update with invalid mode (should raise HTTPException)
        req = UpdateConfigRequest(trading_mode="invalid_mode_name")
        with pytest.raises(HTTPException) as excinfo:
            await update_config(req=req, current_user={"user_id": "12345678-1234-5678-1234-567812345678"})
        assert excinfo.value.status_code == 400
        assert "Invalid trading mode" in excinfo.value.detail

    finally:
        # Restore original configuration
        settings.TRADING_MODE = original_mode
