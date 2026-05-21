import os
from backend.database import SessionLocal, Config
try:
    db = SessionLocal()
    cfg = db.query(Config).first()
    print("Config:", cfg)
except Exception as e:
    print("Error:", e)
