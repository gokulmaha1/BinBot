#!/bin/bash
# BinBot PRO - 1-Click Rebuild Script (Linux/VPS)

echo -e "\033[0;36m🚀 Starting BinBot PRO Rebuild...\033[0m"

# 1. Pull latest code
echo "📥 Pulling latest code..."
git pull origin main

# 2. Stop current containers
echo "🛑 Stopping containers..."
docker compose down

# 3. Clean up Database (Recommended for schema updates)
read -p "Reset database? (y/n): " choice
if [ "$choice" == "y" ]; then
    echo "🧹 Resetting database..."
    rm -f database/trading_bot.db
fi

# 4. Build and Start
echo "🏗️ Rebuilding and Starting..."
docker compose up -d --build

echo -e "\033[0;32m✅ BinBot PRO is now LIVE!\033[0m"
