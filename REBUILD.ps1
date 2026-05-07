# BinBot PRO - 1-Click Rebuild Script (Windows)
Write-Host "🚀 Starting BinBot PRO Rebuild..." -ForegroundColor Cyan

# 1. Pull latest code
Write-Host "📥 Pulling latest code from Git..."
git pull origin main

# 2. Stop current containers
Write-Host "🛑 Stopping containers..."
docker compose down

# 3. Clean up Database (Optional: Only if schema changed)
$choice = Read-Host "Do you want to reset the database? (y/n) [Required if you just added TP/SL columns]"
if ($choice -eq "y") {
    Write-Host "🧹 Resetting database..."
    if (Test-Path "database/trading_bot.db") { Remove-Item "database/trading_bot.db" }
}

# 4. Build and Start
Write-Host "🏗️ Rebuilding and Starting..."
docker compose up -d --build

Write-Host "✅ BinBot PRO is now LIVE!" -ForegroundColor Green
Write-Host "🔗 Dashboard: http://localhost:8012/dashboard/"
pause
