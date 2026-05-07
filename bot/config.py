# Binance API Credentials
USE_TESTNET = True  # Overridden by DB setting at runtime

# LIVE API Keys (Real Money)
LIVE_API_KEY = "t7y8juHl3ms517OTSkaSHkRcKwbxhbB0xcPdoIrwskhRxOQxOExG4uPqvUHwyZBr"
LIVE_API_SECRET = "zGHx5ceVrNii2PBLat4TdJr43M3aN1D255GgwqeHKtgxIA9IWmBOxpz2kqincomF"

# TESTNET API Keys (Simulation) — Generate at https://testnet.binancefuture.com
TESTNET_API_KEY = "R3W5c1x7HSelZRNtj8ChzGadvZNOXCZUK5HyuKknyTIPvCOf0oQ9QRwfKnjORu5J"
TESTNET_API_SECRET = "3YrybsTDc5pSB6554RMXwdorv4g6E52yHqRijUCjZzld7Rng05ZYk9f2ZdUYpI3G"

# Auto-select based on mode (overridden at runtime by DB)
API_KEY = TESTNET_API_KEY if USE_TESTNET else LIVE_API_KEY
API_SECRET = TESTNET_API_SECRET if USE_TESTNET else LIVE_API_SECRET

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
TRAILING_TP_ACTIVATION = 0.01 # 1.0% profit to start trailing
TRAILING_TP_CALLBACK = 0.002  # 0.2% callback from peak


# Technical Indicators
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
