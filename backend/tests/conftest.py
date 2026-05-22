import os
import sys

# Force DATABASE_URL to use async sqlite in-memory database for testing
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["TRADING_MODE"] = "paper"

# Ensure backend directory is in python path
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
