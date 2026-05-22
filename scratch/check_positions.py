import sqlite3
conn = sqlite3.connect('database/trading_bot.db')
cur = conn.cursor()
print('=== TRADES (OPEN/PARTIAL) ===')
for r in cur.execute("SELECT id, symbol, side, status, entry_price, quantity, entry_time FROM trades WHERE status IN ('OPEN', 'PARTIAL_TP')"):
    print(r)
print('=== BOTS ===')
for r in cur.execute("SELECT id, status, trades_today, daily_pnl FROM bots"):
    print(r)
conn.close()
