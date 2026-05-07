# Binance API Credentials
USE_TESTNET = True
API_KEY = "t7y8juHl3ms517OTSkaSHkRcKwbxhbB0xcPdoIrwskhRxOQxOExG4uPqvUHwyZBr"
API_SECRET = "zGHx5ceVrNii2PBLat4TdJr43M3aN1D255GgwqeHKtgxIA9IWmBOxpz2kqincomF"

# Trading Settings
# SYMBOL is now managed dynamically via the Dashboard Watchlist (DB-driven)
LEVERAGE = 20
TIMEFRAME = "1m"

# Risk Management (AUTO-COMPOUNDING MODE)
USE_DYNAMIC_INVESTMENT = True
DYNAMIC_RISK_PCT = 0.50  # Use 50% of wallet balance per trade
LEVERAGE = 20
TAKE_PROFIT = 0.01    # 1.0% price move = 20% ROI at 20x
STOP_LOSS = 0.015      # 1.5% price move = 30% Loss (Tight)
DAILY_LOSS_LIMIT = 200.0
DCA = True             # Enable/Disable Averaging Down
TRAILING_STOP_LOSS = True # Enable/Disable Profit Locking Levels


# Technical Indicators
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
