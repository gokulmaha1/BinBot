import asyncio
import sys
import os

# Add backend app to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    # Use remote IP for connecting
    db_url = "postgresql+asyncpg://binbot:binbot@69.62.83.238:5434/binbot"
    print(f"Connecting to database at {db_url}...")
    engine = create_async_engine(db_url)
    
    async with engine.connect() as conn:
        print("Connected successfully!")
        
        # List tables
        res = await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
        tables = [row[0] for row in res]
        print(f"Tables in DB: {tables}")
        
        # Query bots
        if 'bots' in tables:
            # Get columns of bots table
            res = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='bots'"))
            columns = [row[0] for row in res]
            print(f"Columns in bots table: {columns}")
            
            # Dynamically build select
            selected_cols = [c for c in ['id', 'status', 'trades_today', 'consecutive_losses', 'trading_mode'] if c in columns]
            res = await conn.execute(text(f"SELECT {', '.join(selected_cols)} FROM bots"))
            print("\n--- Bots ---")
            for row in res:
                row_dict = dict(zip(selected_cols, row))
                print(row_dict)
                
        # Query exchange accounts
        if 'exchange_accounts' in tables:
            res = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='exchange_accounts'"))
            columns = [row[0] for row in res]
            print(f"Columns in exchange_accounts table: {columns}")
            res = await conn.execute(text("SELECT id, exchange, mode, is_active FROM exchange_accounts"))
            print("\n--- Exchange Accounts ---")
            for row in res:
                print(f"Account ID: {row[0]}, Exchange: {row[1]}, Mode: {row[2]}, Active: {row[3]}")
                
        # Query latest logs
        if 'logs' in tables:
            res = await conn.execute(text("SELECT created_at, level, source, message FROM logs WHERE level::text IN ('ERROR', 'WARNING') ORDER BY created_at DESC LIMIT 30"))
            print("\n--- Latest DB Warnings/Errors ---")
            for row in res:
                print(f"[{row[0]}] {row[1]} | {row[2]} | {row[3]}")

    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
