# Binance Trading Bot (Production Level)

A robust Binance Futures trading bot implemented in Python, following a modular architecture for EMA cross and RSI signals.

## Project Structure
- `bot/main.py`: Core entry point and loop.
- `bot/config.py`: Configuration (API Keys, Pairs, Risk).
- `bot/strategy.py`: EMA Cross + RSI Signal logic.
- `bot/risk.py`: Position sizing and TP/SL calculations.
- `bot/execution.py`: Binance API order execution.
- `bot/state.py`: Position tracking.
- `bot/logger.py`: Production-level logging.

## Setup Instructions

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure API Keys**
   Open `bot/config.py` and replace `your_api_key` and `your_api_secret` with your actual Binance Futures API credentials.

3. **Run the Bot**
   ```bash
   python bot/main.py
   ```

## Trading Rules (Pre-configured)
- **Pair**: XRPUSDT
- **Leverage**: 15x
- **Capital**: ₹1000 (~$12)
- **Risk**: 2% per trade
- **TP**: 0.8% | **SL**: 0.4%

## Safety Checklist
- [ ] Use Binance Testnet first.
- [ ] Ensure API keys have "Futures" enabled.
- [ ] Restrict API access to your IP address.
- [ ] Monitor the first few trades manually.
