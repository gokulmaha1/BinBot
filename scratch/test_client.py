from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

print("Sending login request...")
response = client.post("/api/login", json={"username": "admin", "password": "binbot_sniper_2026"})
print(response.status_code)
print(response.json())
