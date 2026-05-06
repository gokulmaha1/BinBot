from fastapi import FastAPI, BackgroundTasks, Depends, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from binance.client import Client
from binance import AsyncClient, BinanceSocketManager
from dotenv import load_dotenv
import os
import asyncio
import pandas as pd

# Internal Imports
from backend.database import init_db, SessionLocal, Trade, LogEntry, Config
from backend.execution import ExecutionEngine
from backend.strategy import HybridStrategy
from backend.notification import NotificationService
from bot import config

# Time Helpers
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.now(IST)

load_dotenv()

app = FastAPI(title="BinBot Pro Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import FileResponse

# Global Sniper State
bot_running = False
bot_task = None
latest_confidence = 0.5
LATEST_PRICES = {"LABUSDT": 0.0}
connected_clients = []


# PROTECTED FRONTEND ROUTES
@app.get("/dashboard")
@app.get("/dashboard/")
async def serve_dashboard(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/dashboard/login.html")
    return FileResponse("frontend/index.html")

@app.get("/dashboard/login.html")
async def serve_login():
    return FileResponse("frontend/login.html")

# Serve specific assets manually to ensure total control
@app.get("/dashboard/index.html")
async def serve_index(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/dashboard/login.html")
    return FileResponse("frontend/index.html")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Authentication Helper
def is_authenticated(request: Request):
    auth_cookie = request.cookies.get("binbot_session")
    return auth_cookie == "active_sniper_session"

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(start_socket_feed())

@app.get("/")
async def read_root():
    return {"status": "BinBot Pro Backend is Live"}

@app.post("/api/login")
async def login(data: dict, response: Response):
    user = os.getenv("DASHBOARD_USER", "admin")
    pw = os.getenv("DASHBOARD_PASS", "binbot_sniper_2026")
    if data.get("username") == user and data.get("password") == pw:
        response.set_cookie(key="binbot_session", value="active_sniper_session", httponly=True)
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/logout")
async def logout(response: Response):
    response.delete_cookie("binbot_session")
    return {"status": "logged_out"}

@app.get("/api/trades")
def get_trades(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return db.query(Trade).order_by(Trade.entry_time.desc()).all()

@app.get("/api/logs")
def get_logs(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return db.query(LogEntry).order_by(LogEntry.timestamp.desc()).limit(200).all()

@app.get("/api/stats")
def get_stats(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    ist_today_start = get_ist_now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Filter for TODAY only
    trades = db.query(Trade).filter(Trade.exit_time >= ist_today_start).all()
    total_trades = len(trades)
    win_rate = 0
    today_pnl = sum([t.pnl for t in trades if t.pnl])
    
    if total_trades > 0:
        wins = len([t for t in trades if t.pnl and t.pnl > 0])
        win_rate = (wins / total_trades) * 100
    
    # Fetch actual balance
    balance = 0
    try:
        client = Client(config.API_KEY, config.API_SECRET)
        acc_info = client.futures_account_balance()
        balance = float(next(b['balance'] for b in acc_info if b['asset'] == 'USDT'))
    except Exception as e:
        print(f"Balance fetch error: {e}")

    return {
        "total_trades": total_trades,
        "win_rate": f"{win_rate:.2f}%",
        "total_pnl": f"${today_pnl:.2f}",
        "wallet_balance": f"${balance:.2f}",
        "latest_confidence": round(latest_confidence * 100, 2)
    }

# Initialize Services
strategy = HybridStrategy()
notifier = NotificationService()

# Global State
bot_running = False
bot_task = None
pnl_reset_time = None
latest_confidence = 0.5
LATEST_PRICES = {"LABUSDT": 0.0}
connected_clients = []

@app.websocket("/ws/price")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep alive
    except WebSocketDisconnect:
        connected_clients.remove(websocket)

@app.get("/api/price")
def get_price():
    return {"price": LATEST_PRICES.get("LABUSDT", 0.0)}

async def start_socket_feed():
    """Ultra-High Speed Trade Stream with REST Fallback"""
    while True:
        client = None
        try:
            log("SOCKET: Connecting to High-Speed Trade Stream (LABUSDT)...", "info")
            client = await AsyncClient.create(config.API_KEY, config.API_SECRET)
            bm = BinanceSocketManager(client)
            # Use AggTrade for millisecond-level precision
            ts = bm.aggtrade_socket('labusdt')
            
            async with ts as tscm:
                log("SOCKET: Connected to Trade Stream.", "success")
                while True:
                    res = await tscm.recv()
                    if res and 'p' in res:
                        price = float(res['p'])
                        LATEST_PRICES['LABUSDT'] = price
                        # log(f"STREAM DATA: {price}", "info") # Debug
                        # Broadcast to UI clients
                        msg = json.dumps({"symbol": "LABUSDT", "price": price})
                        for client_ws in connected_clients:
                            try:
                                await client_ws.send_text(msg)
                            except:
                                pass
        except Exception as e:
            log(f"SOCKET ALERT: {e}. Switching to REST polling...", "error")
            try:
                if not client: client = await AsyncClient.create(config.API_KEY, config.API_SECRET)
                ticker = await asyncio.to_thread(client.futures_symbol_ticker, symbol="LABUSDT")
                LATEST_PRICES['LABUSDT'] = float(ticker['price'])
            except: pass
            await asyncio.sleep(5)
            if client:
                try: await client.close_connection()
                except: pass
            await asyncio.sleep(5)

async def bot_loop():
    global bot_running
    log("Initializing Bot Loop...", "info")
    
    # Binance Client
    try:
        client = Client(config.API_KEY, config.API_SECRET)
        log("Binance Client Initialized Successfully", "info")
    except Exception as e:
        log(f"Connection Failed: {e}", "error")
        bot_running = False
        return

    executor = ExecutionEngine(client)
    
    SYMBOLS = ["LABUSDT"]
    price_histories = {s: [] for s in SYMBOLS}
    consecutive_losses = 0
    cooldown_until = None
    
    # 1. Background Scanner Task (DEPRECATED - Using Fixed Pairs)
    async def run_scanner():
        log("Dynamic Scanner: [OFFLINE] - Using Hardened List: LABUSDT ONLY", "info")

    log(f"Bot Heartbeat: [ONLINE]. Sniper engaging: {', '.join(SYMBOLS)}", "warning")
    
    while bot_running:
        try:
            # 1. IRON SHIELD: Global Daily Loss Limit Check
            db = SessionLocal()
            cfg = db.query(Config).first()
            # Start tracking from today 00:00 OR the last manual reset time
            ist_today_start = get_ist_now().replace(hour=0, minute=0, second=0, microsecond=0)
            tracking_start = ist_today_start
            if pnl_reset_time and pnl_reset_time > ist_today_start:
                tracking_start = pnl_reset_time
                
            daily_trades = db.query(Trade).filter(Trade.exit_time >= tracking_start, Trade.status == "CLOSED").all()
            daily_pnl = sum([t.pnl for t in daily_trades if t.pnl])
            
            if daily_pnl < -cfg.daily_loss_limit:
                log(f"SHIELD ACTIVE: Daily Loss Limit (-${abs(daily_pnl):.2f}) hit. Bot is now IDLE for safety.", "warning")
                bot_running = False
                db.close()
                break

            any_open = db.query(Trade).filter(Trade.status == "OPEN").first()
            
            # 2b. COOLDOWN CHECK (30s)
            if not any_open:
                last_closed = db.query(Trade).filter(Trade.status == "CLOSED").order_by(Trade.exit_time.desc()).first()
                if last_closed and last_closed.exit_time:
                    now = datetime.now(IST)
                    diff = (now - last_closed.exit_time.replace(tzinfo=IST)).total_seconds()
                    if diff < 30:
                        db.close()
                        await asyncio.sleep(5)
                        continue
            db.close()

            
            if daily_pnl < -config.DAILY_LOSS_LIMIT: 
                log(f"IRON SHIELD: Daily Loss Limit (-${config.DAILY_LOSS_LIMIT}) hit. Emergency Stop.", "error")

                bot_running = False
                break
                
            if any_open:
                # 1. Fetch Current Price immediately for management logic
                ticker = await asyncio.to_thread(client.futures_symbol_ticker, symbol=any_open.symbol)
                curr_p = float(ticker['price'])

                # 2. IRON SHIELD: Verification Check
                is_sl_active = await asyncio.to_thread(executor.verify_sl_active, any_open.symbol)
                if not is_sl_active:
                    log(f"SHIELD: Re-applying protection for {any_open.symbol}...", "warning")
                    await asyncio.to_thread(executor.set_tp_sl, any_open.symbol, any_open.side, any_open.entry_price, cfg.take_profit, cfg.stop_loss)

                # 3. Check if position actually exists on Binance
                pos = await asyncio.to_thread(client.futures_position_information, symbol=any_open.symbol)
                current_qty = 0
                for p in pos:
                    if p['symbol'] == any_open.symbol:
                        current_qty = float(p['positionAmt'])
                        break
                
                # 4. If position is ZERO, it was closed by TP/SL
                if abs(current_qty) < 0.00000001:
                    log(f"DETECTION: Position for {any_open.symbol} is CLOSED on Binance. Syncing DB...", "warning")
                    db = SessionLocal()
                    t = db.query(Trade).filter(Trade.id == any_open.id).first()
                    t.status = "CLOSED"
                    t.exit_time = datetime.now(IST)
                    
                    # Fetch final PnL
                    try:
                        income = await asyncio.to_thread(client.futures_income_history, symbol=any_open.symbol, incomeType="REALIZED_PNL", limit=1)
                        if income: t.pnl = float(income[0]['income'])
                    except: 
                        pass
                    
                    # Update Cooldown Logic
                    if t.pnl and t.pnl < 0:
                        consecutive_losses += 1
                        if consecutive_losses == 2:
                            cooldown_until = datetime.now(IST) + timedelta(minutes=10)
                            log("SHIELD: 2 consecutive losses. 10-minute cooldown active.", "error")
                        elif consecutive_losses >= 3:
                            cooldown_until = datetime.now(IST) + timedelta(minutes=30)
                            log("SHIELD: 3 consecutive losses. 30-minute cooldown active.", "error")
                    else:
                        if consecutive_losses > 0:
                            log(f"SHIELD: Win detected! Resetting loss counter. (Was: {consecutive_losses})", "info")
                        consecutive_losses = 0 # Reset on win
                        
                    db.commit()
                    db.close()
                    log(f"SUCCESS: Trade {any_open.id} marked as CLOSED. Returning to scan mode.", "warning")
                    continue

                # 5. DCA RECOVERY (Manage Loss)
                pnl_pct = ((curr_p - any_open.entry_price) / any_open.entry_price * 100) * (1 if any_open.side == "BUY" else -1)
                
                # If loss exceeds -0.6% and we haven't DCA'd yet
                if cfg.dca_enabled and pnl_pct < -0.6 and any_open.fee < 10: 
                    log(f"DCA RECOVERY: Firing RECOVERY order for {any_open.symbol}...", "warning")
                    
                    # FIX: Correctly round the DCA Quantity
                    qty_precision = executor.get_quantity_precision(any_open.symbol)
                    dca_qty = round(any_open.quantity * 0.8, qty_precision)
                    
                    if qty_precision == 0: dca_qty = int(dca_qty)
                    
                    order, error = executor.place_market_order(any_open.symbol, any_open.side, dca_qty)
                    
                    if order:
                        # Update DB entry to reflect new state
                        db = SessionLocal()
                        t = db.query(Trade).filter(Trade.id == any_open.id).first()
                        t.quantity += dca_qty
                        t.fee = 15 # Flag to prevent multiple DCA
                        
                        # Recalculate average entry (approximation)
                        new_entry = (any_open.entry_price * any_open.quantity + curr_p * dca_qty) / (any_open.quantity + dca_qty)
                        t.entry_price = new_entry
                        
                        # Move TP to Break-Even + 0.1% (Safe Exit)
                        executor.set_tp_sl(any_open.symbol, any_open.side, new_entry, 0.001, cfg.stop_loss)
                        
                        db.commit()
                        db.close()
                        log(f"RECOVERY SUCCESS: Average Entry lowered to {new_entry:.4f}. TP moved to BREAK-EVEN.", "warning")
                    else:
                        log(f"RECOVERY FAILED: {error}", "error")

                # 6. DYNAMIC SMART MANAGEMENT (Trend Riding & TP Extension)
                now = datetime.now(IST)
                duration_mins = (now - any_open.entry_time.replace(tzinfo=IST)).total_seconds() / 60
                
                if cfg.trailing_sl_enabled:
                    # TIER 1: BREAK-EVEN LOCK (+0.4% move)
                    if pnl_pct > 0.4 and any_open.fee < 5:
                        log(f"SMART SHIELD: Moving SL to BREAK-EVEN for {any_open.symbol}.", "warning")
                        await asyncio.to_thread(executor.set_tp_sl, any_open.symbol, any_open.side, any_open.entry_price, cfg.take_profit, 0, absolute_sl=any_open.entry_price)
                        db = SessionLocal()
                        t = db.query(Trade).filter(Trade.id == any_open.id).first()
                        t.fee = 5 # BE Protected
                        db.commit(); db.close()

                    # TIER 2: PROFIT LOCK (+1.0% move)
                    elif pnl_pct > 1.0 and any_open.fee < 10:
                        # Lock in 0.5% net profit behind the trend
                        is_buy = any_open.side == "BUY"
                        lock_price = any_open.entry_price * (1.005 if is_buy else 0.995)
                        log(f"MOONSHOT LOCK: Protecting +0.5% profit on {any_open.side} for {any_open.symbol}.", "success")
                        await asyncio.to_thread(executor.set_tp_sl, any_open.symbol, any_open.side, any_open.entry_price, cfg.take_profit + 0.01, 0, absolute_sl=lock_price)
                        db = SessionLocal()
                        t = db.query(Trade).filter(Trade.id == any_open.id).first()
                        t.fee = 10 # Tier 2 Protected
                        db.commit(); db.close()

                    # TIER 3: TP EXTENSION (Catching the Spike/Dump)
                    # If we are close to TP and momentum is still accelerating, PUSH TP!
                    if pnl_pct > (cfg.take_profit * 100 - 0.2) and abs(mom_30s) > 0.005:
                        new_tp = cfg.take_profit + 0.015 # Push TP by another 1.5%
                        log(f"MOMENTUM SNIPER: Extending TP to {new_tp*100:.1f}% to ride the {'PUMP' if pnl_pct > 0 else 'DUMP'}!", "success")
                        await asyncio.to_thread(executor.set_tp_sl, any_open.symbol, any_open.side, any_open.entry_price, new_tp, cfg.stop_loss)

                # B. TIME-BASED ESCAPE (Reduce Opportunity Loss)
                elif cfg.trailing_sl_enabled and duration_mins > 5 and 0.1 < pnl_pct < cfg.take_profit * 100 and any_open.fee < 20:
                    log(f"TIME ESCAPE: Trade is slow ({duration_mins:.1f}m). Reducing TP to 0.2% for quick exit.", "warning")
                    await asyncio.to_thread(executor.set_tp_sl, any_open.symbol, any_open.side, any_open.entry_price, 0.002, cfg.stop_loss)
                    db = SessionLocal()
                    t = db.query(Trade).filter(Trade.id == any_open.id).first()
                    t.fee = 20 # Mark as Time-Exited
                    db.commit()
                    db.close()

                # C. LOGGING STATUS (Periodic)
                if int(now.second) % 30 < 5: # Log every 30s approx
                    log(f"MANAGING: {any_open.symbol} {any_open.side} | PnL: {pnl_pct:+.2f}% | Age: {duration_mins:.1f}m | State: {any_open.fee}", "info")
                
                await asyncio.sleep(5)
                continue


            # 3. Smart Risk Engine (Global Threshold)
            db = SessionLocal()
            recent_trades = db.query(Trade).order_by(Trade.id.desc()).limit(5).all()
            db.close()
            required_confidence = 0.60 
            if len(recent_trades) >= 3:
                losses = [t for t in recent_trades if t.pnl and t.pnl < 0]
                if len(losses) >= 2: required_confidence = 0.70
                elif len(losses) == 0: required_confidence = 0.55


            # 4. TRIPLE SNIPE: Loop through Symbols
            for symbol in SYMBOLS:
                try:
                    # Fetch Data (High Speed WebSocket with REST Fallback)
                    curr_price = LATEST_PRICES.get(symbol, 0.0)
                    if curr_price == 0:
                        ticker = await asyncio.to_thread(client.futures_symbol_ticker, symbol=symbol)
                        curr_price = float(ticker['price'])
                    
                    history = price_histories[symbol]
                    history.append(curr_price)
                    if len(history) > 30: history.pop(0)
                    
                    # 4. Fetch Technical Data (1m, 5m, 15m)
                    klines_1m = await asyncio.to_thread(client.futures_klines, symbol=symbol, interval='1m', limit=200)
                    klines_5m = await asyncio.to_thread(client.futures_klines, symbol=symbol, interval='5m', limit=200)
                    klines_15m = await asyncio.to_thread(client.futures_klines, symbol=symbol, interval='15m', limit=200)
                    
                    df = pd.DataFrame(klines_1m, columns=['time','open','high','low','close','vol','ct','qv','nt','tb','tv','ig'])
                    df_5m = pd.DataFrame(klines_5m, columns=['time','open','high','low','close','vol','ct','qv','nt','tb','tv','ig'])
                    df_15m = pd.DataFrame(klines_15m, columns=['time','open','high','low','close','vol','ct','qv','nt','tb','tv','ig'])
                    
                    for d in [df, df_5m, df_15m]:
                        for col in ['open', 'high', 'low', 'close', 'vol']: d[col] = d[col].astype(float)
                        d['volume'] = d['vol']

                    # 5. Accurate 30s Momentum & Volume Spike Detection
                    mom_30s = (curr_price - history[0]) / history[0] if len(history) >= 30 else 0
                    
                    # 5. Volatility Brake (Smart Chaos Filter)
                    is_volatile = abs(mom_30s) > 0.015
                    if abs(mom_30s) > 0.04: # Hard Stop for extreme chaos (Flash Crash)
                        cooldown_until = datetime.now(IST) + timedelta(minutes=5)
                        log(f"SHIELD: Extreme Chaos detected ({abs(mom_30s)*100:.2f}%). Emergency Pause.", "error")
                        break
                    
                    if is_volatile:
                        log(f"ALERT: High Volatility detected ({abs(mom_30s)*100:.2f}%). Awaiting AI Approval...", "info")

                    avg_vol = df['volume'].rolling(20).mean().iloc[-1]
                    curr_vol = df['volume'].iloc[-1]
                    vol_spike = curr_vol / avg_vol if avg_vol > 0 else 1.0

                    # 6. Check Strategy (Triple-Lock: Confluence + Volume + 30s Mom)
                    velocity = (curr_price - history[0]) / history[0] if len(history) >= 30 else 0
                    signal, confidence = strategy.get_signal_with_confluence(
                        df, df_5m, df_15m, 
                        velocity=velocity, 
                        vol_spike=vol_spike, 
                        mom_30s=mom_30s
                    )
                    
                    if signal:
                        # 7. AI PROBABILITY LAYER
                        ai_data = {
                            "rsi": df['rsi'].iloc[-1] if 'rsi' in df else 50,
                            "ema_spread": (df['ema9'].iloc[-1] - df['ema21'].iloc[-1]) / df['ema21'].iloc[-1] if 'ema9' in df else 0,
                            "vol_spike": vol_spike,
                            "mom_30s": mom_30s
                        }
                        # ... (API Data Fetching remains same) ...
                        try:
                            # Order Book Imbalance
                            depth = await asyncio.to_thread(client.futures_order_book, symbol=symbol, limit=20)
                            bid_vol = sum([float(b[1]) for b in depth['bids']])
                            ask_vol = sum([float(a[1]) for a in depth['asks']])
                            ai_data['ob_imbalance'] = bid_vol / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0.5
                            btc = await asyncio.to_thread(client.futures_symbol_ticker, symbol="BTCUSDT")
                            ai_data['btc_trend'] = "bullish" if float(btc['price']) > 50000 else "neutral"
                            liqs = await asyncio.to_thread(client.futures_liquidation_orders, symbol=symbol, limit=5)
                            ai_data['liquidation_data'] = len(liqs) > 0
                        except: pass
                        
                        ai_report = strategy.ai_model.get_trade_quality_score(ai_data)
                        
                        # MOONSHOT OVERRIDE: If AI is confident, ignore volatility brake
                        if is_volatile and ai_report['confidence'] >= 0.80:
                            log(f"SNIPER OVERRIDE: High Volatility confirmed as MOONSHOT! AI Confidence: {ai_report['confidence']*100}%", "success")
                        elif is_volatile:
                            log(f"SHIELD: Volatility too high for this setup. Skipping for safety.", "warning")
                            continue
                        else:
                            # Standard AI Filter
                            log(f"AI JURY: Confidence {ai_report['confidence']*100}% | Risk: {ai_report['risk_level'].upper()} | Regime: {ai_report['market_regime']}", "warning")
                            if ai_report['risk_level'] == "high" or ai_report['confidence'] < 0.55:
                                log(f"AI SHIELD: Trade Rejected (Quality Score: {ai_report['confidence']})", "info")
                                continue

                        # 8. REVENGE SHIELD: Final Cooldown Watch
                        is_high_conviction = ai_report['confidence'] >= 0.90
                        if cooldown_until and datetime.now(IST) < cooldown_until and not is_high_conviction:
                            remaining = (cooldown_until - datetime.now(IST)).total_seconds()
                            if int(now.second) % 10 == 0: # Log every 10s
                                log(f"ACTIVE WATCH: {signal} detected ({ai_report['confidence']*100:.1f}% AI Confidence). [REVENGE COOLDOWN: {int(remaining)}s remaining]", "info")
                            continue
                        elif is_high_conviction and cooldown_until and datetime.now(IST) < cooldown_until:
                            log(f"SNIPER BYPASS: High Conviction ({ai_report['confidence']*100}%) detected. Ignoring cooldown to strike!", "success")

                        # 9. Final Validation & Strike
                        is_valid = ai_report['confidence'] >= required_confidence
                        
                        # Emergency Force-Valid for 90%+ (High Conviction)
                        if ai_report['confidence'] >= 0.90: is_valid = True

                        if is_valid:
                            # Dynamic Leverage Override (AI recommendation)
                            active_leverage = min(cfg.leverage, ai_report['recommended_leverage'])
                            log(f"AI SNIPER: Engaging {signal} at {active_leverage}x leverage. Confidence: {ai_report['confidence']*100}%", "warning")
                            # 1. Calculate Quantity (Dynamic Auto-Compounding)
                            try:
                                acc_info = await asyncio.to_thread(client.futures_account_balance)
                                balance_item = next(b for b in acc_info if b['asset'] == 'USDT')
                                wallet_usdt = float(balance_item['balance'])
                                
                                if wallet_usdt < 5.0:
                                    log(f"SHIELD: Balance too low (${wallet_usdt:.2f}). Minimum $5 required.", "warning")
                                    continue
                                    
                                risk_pct = cfg.dynamic_risk_pct if cfg.use_dynamic else 0.50 # default
                                # REVENGE SHIELD: Reduce size on losing streaks
                                if consecutive_losses == 1: 
                                    risk_pct *= 0.75
                                    log("SHIELD: Reducing trade size by 25% due to recent loss.", "info")
                                elif consecutive_losses >= 2:
                                    risk_pct *= 0.50
                                    log("SHIELD: Reducing trade size by 50% due to losing streak.", "warning")
                                    
                                investment = wallet_usdt * risk_pct
                                # 2. Sync Leverage to Binance
                                try:
                                    await asyncio.to_thread(client.futures_change_leverage, symbol=symbol, leverage=active_leverage)
                                except: pass
                                
                                target_value = investment * active_leverage 
                                raw_qty = target_value / curr_price
                                log(f"DYNAMIC: Scaling trade to ${investment:.2f} ({risk_pct*100}% of ${wallet_usdt:.2f} balance)", "info")
                            except Exception as e:
                                log(f"DYNAMIC ERROR: Could not fetch balance ({e}). Using safety fallback $5.", "error")
                                target_value = 5.0 * active_leverage
                                raw_qty = target_value / curr_price

                            precision = executor.get_quantity_precision(symbol)
                            qty = round(raw_qty, precision)
                            
                            if qty <= 0:
                                log(f"Execution Failed: Calculated Qty is 0. Check Balance!", "error")
                                continue

                            # 2. Execute Market Order
                            order, error = executor.place_market_order(symbol, signal, qty)
                            
                            if order:
                                log(f"TRADE EXECUTED: {signal} {qty} {symbol} at Market", "warning")
                                
                                # 3. Set TP/SL
                                success = executor.set_tp_sl(symbol, signal, curr_price, cfg.take_profit, cfg.stop_loss)
                                if success:
                                    # Calculate Net Estimates
                                    est_profit = target_value * cfg.take_profit
                                    est_fee = target_value * 0.001 # 0.1% roundtrip
                                    net_profit = est_profit - est_fee
                                    log(f"SHIELD ACTIVE: TP {cfg.take_profit*100}% | SL: {cfg.stop_loss*100}%", "info")
                                    log(f"FEE CHECK: Est. Profit: ${net_profit:.2f} (After ${est_fee:.2f} fees)", "info")
                                else:
                                    log(f"Warning: TP/SL Order failed to place!", "error")


                                # 4. Save to Database
                                db = SessionLocal()
                                entry_fee = curr_price * qty * 0.0005
                                new_trade = Trade(
                                    symbol=symbol, side=signal, leverage=active_leverage,
                                    entry_price=curr_price, quantity=qty, fee=entry_fee, status="OPEN"
                                )
                                db.add(new_trade)
                                db.commit()
                                db.close()
                                break 
                            else:
                                log(f"Order Error: {error}", "error")
 # Exit symbol loop
                except Exception as e:
                    if "NameResolutionError" in str(e) or "HTTPSConnectionPool" in str(e):
                        log(f"NETWORK ALERT: Connection lost. Retrying in 10s...", "error")
                        await asyncio.sleep(10)
                    else:
                        log(f"Loop Error ({symbol}): {e}", "error")

            await asyncio.sleep(1)
        except Exception as e:
            log(f"Main Loop Error: {e}", "error")
            await asyncio.sleep(10)

def log(message, level="info"):
    db = SessionLocal()
    new_log = LogEntry(message=message, level=level)
    db.add(new_log)
    db.commit()
    db.close()
    print(f"[{level.upper()}] {message}")

async def sync_trades_background():
    """Background task to sync trade status with Binance"""
    client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
    while True:
        try:
            db = SessionLocal()
            open_trades = db.query(Trade).filter(Trade.status == "OPEN").all()
            
            if open_trades:
                # Get current positions from Binance
                positions = client.futures_position_information()
                pos_map = {p['symbol']: float(p['positionAmt']) for p in positions}
                
                for trade in open_trades:
                    current_qty = pos_map.get(trade.symbol, 0)
                    
                    if abs(current_qty) > 0.00000001:
                        # Profit Guard Logic (Trailing SL)
                        try:
                            ticker = client.futures_symbol_ticker(symbol=trade.symbol)
                            price = float(ticker['price'])
                            entry = trade.entry_price
                            side = trade.side
                            p_diff = (price - entry) / entry if side == 'BUY' else (entry - price) / entry
                            
                            # LEVEL 0: Break-Even at +6% ROI (0.3% price move)
                            if p_diff >= 0.003 and trade.fee < 0.5:
                                log(f"IRON SHIELD: Price hit +6% ROI on {trade.symbol}. Moving SL to BREAK-EVEN.", "warning")
                                new_sl = entry * 1.0005 if side == 'BUY' else entry * 0.9995
                                executor.set_tp_sl(trade.symbol, side, entry, config.TAKE_PROFIT, 0, absolute_sl=new_sl)
                                trade.fee = 0.5 
                            
                            # LEVEL 1: Profit Lock at +15% ROI (0.75% price move)
                            elif p_diff >= 0.0075 and trade.fee < 1.0:
                                log(f"PROFIT LOCK: Price hit +15% ROI on {trade.symbol}. Locking in +5% profit.", "warning")
                                new_sl = entry * 1.0025 if side == 'BUY' else entry * 0.9975
                                executor.set_tp_sl(trade.symbol, side, entry, config.TAKE_PROFIT, 0, absolute_sl=new_sl)
                                trade.fee = 1.0
                            
                            # LEVEL 2: PROFIT EXTENSION (Moon Shot)
                            # If price hits 90% of our TP target, extend TP and move SL to lock original profit
                            elif p_diff >= (config.TAKE_PROFIT * 0.9) and trade.fee < 2.0:
                                extended_tp = config.TAKE_PROFIT + 0.005 # Add 0.5% to target
                                lock_sl_price = entry * (1 + config.TAKE_PROFIT * 0.8) if side == 'BUY' else entry * (1 - config.TAKE_PROFIT * 0.8)
                                log(f"MOON SHOT: {trade.symbol} nearing TP! Extending target and locking SL at 80% of original TP.", "warning")
                                executor.set_tp_sl(trade.symbol, side, entry, extended_tp, 0, absolute_sl=lock_sl_price)
                                trade.fee = 2.0 

                            # LEVEL 3: Extreme Trail (+2.5% Move / +50% ROI)
                            elif p_diff >= 0.025 and trade.fee < 3.0:
                                log(f"EXTREME TRAIL: Price hit +50% ROI! Locking in +30% profit...", "warning")
                                new_sl = entry * 1.015 if side == 'BUY' else entry * 0.985
                                executor.set_tp_sl(trade.symbol, side, entry, config.TAKE_PROFIT + 0.01, 0, absolute_sl=new_sl)
                                trade.fee = 3.0
                        except Exception as e:
                            print(f"[TRAIL] Error: {e}")
                        continue

                    # If position is 0, it means it was closed by TP or SL
                    if abs(current_qty) < 0.00000001:
                        # Attempt to fetch closing details
                        try:
                            income = client.futures_income_history(symbol=trade.symbol, incomeType="REALIZED_PNL", limit=1)
                            user_trades = client.futures_account_trades(symbol=trade.symbol, limit=1)
                            
                            pnl = float(income[0]['income']) if income else 0
                            exit_price = float(user_trades[0]['price']) if user_trades else 0
                            
                            trade.status = "CLOSED"
                            trade.exit_price = exit_price
                            trade.pnl = pnl
                            trade.exit_time = get_ist_now()
                            log(f"Background Sync: Trade Closed {trade.symbol} | PnL: ${pnl:.2f}", "warning")
                        except Exception as e:
                            # Still close the trade if position is zero, even if stats fetch fails
                            trade.status = "CLOSED"
                            trade.exit_time = get_ist_now()
                            log(f"Background Sync: Trade Closed {trade.symbol} (Stats fetch failed: {e})", "warning")

                
                db.commit()
            db.close()
        except Exception as e:
            print(f"[SYNC] Error: {e}")
        
        await asyncio.sleep(5) # Check every 5 seconds

@app.post("/api/bot/start")
async def start_bot(background_tasks: BackgroundTasks):
    global bot_running
    log("API: Received Start Bot Request", "info")
    if not bot_running:
        bot_running = True
        background_tasks.add_task(bot_loop)
        background_tasks.add_task(sync_trades_background)
        return {"message": "Bot started"}
    return {"message": "Bot is already running"}

@app.get("/api/bot/status")
def get_bot_status():
    return {"running": bot_running}

@app.post("/api/bot/stop")
async def stop_bot():
    global bot_running
    log("API: Received Stop Bot Request", "warning")
    bot_running = False
    return {"message": "Bot stopped"}

@app.post("/api/bot/reset_pnl")
async def reset_pnl():
    global pnl_reset_time
    pnl_reset_time = get_ist_now()
    log(f"MANUAL RESET: Daily PnL counter has been reset to zero.", "warning")
    return {"message": "PnL counter reset successfully"}

@app.get("/api/config")
def get_current_config(db: Session = Depends(get_db)):
    cfg = db.query(Config).first()
    return {
        "leverage": cfg.leverage,
        "take_profit": cfg.take_profit * 100,
        "stop_loss": cfg.stop_loss * 100,
        "daily_loss_limit": cfg.daily_loss_limit,
        "use_dynamic": cfg.use_dynamic,
        "dynamic_risk_pct": cfg.dynamic_risk_pct * 100,
        "dca": cfg.dca_enabled,
        "trailing_sl": cfg.trailing_sl_enabled
    }

@app.post("/api/config/update")
async def update_config(data: dict):
    try:
        db = SessionLocal()
        cfg = db.query(Config).first()
        
        # Helper to handle "true"/"false" strings from UI
        def to_bool(val):
            if isinstance(val, bool): return val
            return str(val).lower() == "true"

        cfg.leverage = int(data.get("leverage", 20))
        cfg.take_profit = float(data.get("take_profit", 1.0)) / 100
        cfg.stop_loss = float(data.get("stop_loss", 1.5)) / 100
        cfg.daily_loss_limit = float(data.get("daily_loss_limit", 200))
        cfg.use_dynamic = to_bool(data.get("use_dynamic"))
        cfg.dynamic_risk_pct = float(data.get("dynamic_risk_pct", 50)) / 100
        cfg.dca_enabled = to_bool(data.get("dca"))
        cfg.trailing_sl_enabled = to_bool(data.get("trailing_sl"))
        
        db.commit()
        db.close()
        
        log(f"Config Updated: All risk parameters saved to DB.", "info")
        return {"message": "Config updated in database"}
    except Exception as e:
        log(f"Config Update Error: {e}", "error")
        return {"error": str(e)}
