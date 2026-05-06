from backend.database import SessionLocal, Trade
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

db = SessionLocal()
trade = db.query(Trade).filter(Trade.symbol == "DOGEUSDT", Trade.status == "OPEN").first()

if trade:
    trade.status = "CLOSED"
    trade.exit_price = trade.entry_price # Dummy exit
    trade.pnl = 0.0
    trade.exit_time = datetime.now(IST)
    db.commit()
    print(f"Successfully closed DOGEUSDT trade (ID: {trade.id}) in the database.")
else:
    print("No open DOGEUSDT trade found.")

db.close()
