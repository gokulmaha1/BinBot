import sqlite3
import os

db_path = os.path.join("c:\\Users\\kokul\\binbot", "database", "trading_bot.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS config;")
        conn.commit()
        print("Dropped config table. It will be recreated on next bot restart.")
    except Exception as e:
        print("Error dropping table:", e)
    finally:
        conn.close()
else:
    print(f"Database not found at {db_path}")
