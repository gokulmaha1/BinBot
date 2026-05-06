from backend.database import engine, Base
import os

# Path to the DB file
db_path = "database/trading_bot.db"

if os.path.exists(db_path):
    # Option 1: Delete the file (simplest for a full reset)
    try:
        os.remove(db_path)
        print("Database file deleted successfully.")
    except Exception as e:
        print(f"Error deleting database: {e}")
else:
    print("Database file not found.")

# Re-initialize the DB
from backend.database import init_db
init_db()
print("Database re-initialized fresh.")
