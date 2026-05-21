import requests

try:
    res = requests.post("http://127.0.0.1:8000/api/login", json={"username": "admin", "password": "binbot_sniper_2026"})
    print(res.status_code)
    print(res.json())
except Exception as e:
    print("Error:", e)
