import sqlite3

try:
    conn = sqlite3.connect('database/trading_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    
    # Also attempt alter
    if ('config',) in tables:
        try:
            cursor.execute("ALTER TABLE config ADD COLUMN trailing_tp_enabled BOOLEAN DEFAULT 1;")
            conn.commit()
            print("Column trailing_tp_enabled added.")
        except Exception as e:
            print("Alter error:", e)
    
    conn.close()
except Exception as e:
    print("Error:", e)
