from backend.database import SessionLocal, Trade
db = SessionLocal()
trades = db.query(Trade).filter(Trade.status == "OPEN").all()
for t in trades:
    print(f"ID: {t.id}, Symbol: {t.symbol}, Side: {t.side}, Status: {t.status}")
db.close()
