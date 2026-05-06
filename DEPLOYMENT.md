# 🚀 BinBot PRO: Deployment Guide

Follow these steps to move your bot from development to a 24/7 Production Environment.

---

## **Option 1: Professional VPS Deployment (Recommended)**
*Best for 24/7 trading and zero latency.*

### **1. Server Preparation (Ubuntu 22.04+)**
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.10+ and Pip
sudo apt install python3-pip python3-venv -y

# Install PM2 (Process Manager)
sudo apt install nodejs npm -y
sudo npm install pm2 -g
```

### **2. Clone and Setup**
```bash
# Clone your code (or upload via SFTP)
cd ~/binbot

# Create Virtual Environment
python3 -m venv venv
source venv/bin/activate

# Install Dependencies
pip install -r requirements.txt
```

### **3. Environment Configuration**
Create a `.env` file in the root directory:
```ini
BINANCE_API_KEY=your_actual_key
BINANCE_API_SECRET=your_actual_secret
DATABASE_URL=sqlite:///./trade_bot.db
```

### **4. Launch with PM2 (Auto-Restart)**
PM2 will monitor your bot and restart it instantly if it crashes.
```bash
# Start the Backend & Sniper Engine
pm2 start "venv/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000" --name binbot-engine

# Save PM2 state for reboots
pm2 save
pm2 startup
```

---

## **Option 2: Local Windows Deployment**
*Best for testing or if you have a high-uptime PC.*

1. **Create a Startup Batch File**:
   Create `start_bot.bat` in your project folder:
   ```batch
   @echo off
   call venv\Scripts\activate
   python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
   pause
   ```
2. **Add to Windows Startup**:
   Press `Win + R`, type `shell:startup`, and paste a shortcut to your `.bat` file there.

---

## **🛡️ Security Checklist**
> [!IMPORTANT]
> **1. Firewall (UFW)**: Only allow access to port 8000 from your specific IP address.
> `sudo ufw allow from YOUR_IP to any port 8000`
> 
> **2. API Permissions**: On Binance, ensure your API key has **"Enable Futures"** checked but **"Enable Withdrawals"** UNCHECKED.
> 
> **3. Database Backups**: Regularly copy `trade_bot.db` to a safe location to preserve your trade history.

---

## **Monitoring**
*   **Check Logs**: `pm2 logs binbot-engine`
*   **Monitor Resources**: `pm2 monit`
*   **Stop Bot**: `pm2 stop binbot-engine`
