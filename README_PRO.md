# BinBot PRO (AI-Powered Trading System)

A production-ready trading bot with AI confirmation, modern dashboard, and Telegram integration.

## Architecture
- **Backend**: FastAPI (Python)
- **AI Engine**: XGBoost for signal confirmation (>70% confidence)
- **Database**: SQLite (SQLAlchemy)
- **Frontend**: TailwindCSS + Alpine.js Dashboard
- **Notification**: Telegram Bot API
- **Deployment**: Docker + Docker Compose

## Features
- **Hybrid Strategy**: Combines EMA Cross, RSI, and Volume with AI sentiment.
- **Risk Management**: Isolated margin, automatic TP/SL, and position sizing.
- **Real-time Monitoring**: PnL tracking and trade history on a glassmorphism dashboard.
- **Alerts**: Instant Telegram notifications for every trade.

## Setup

1. **Install Dependencies**
   ```bash
   pip install -r backend/requirements.txt
   ```

2. **Configure Environment**
   Update `.env` with your Binance API keys and Telegram credentials.

3. **Run the Backend**
   ```bash
   uvicorn backend.main:app --reload
   ```

4. **Open the Dashboard**
   Open `frontend/index.html` in your browser.

5. **Start Trading**
   Click "Start Bot" on the dashboard.

## AI Model
The bot uses a pre-trained XGBoost model in `ai_engine/model.py`. You can retrain it by providing new historical data to the `train()` method.

## Deployment (Docker)
```bash
docker-compose up --build
```
