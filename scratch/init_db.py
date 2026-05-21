import os
os.environ["DATABASE_URL"] = "sqlite:///./database/trading_bot.db"

from backend.database import init_db
print("Initializing DB...")
init_db()
print("Done.")

import sqlite3
conn = sqlite3.connect('database/trading_bot.db')
print("Tables:", conn.cursor().execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall())
conn.close()
