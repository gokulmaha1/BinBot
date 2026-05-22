/**
 * BinBot PRO - Overview Page Script
 */

let activeSymbol = 'BTCUSDT';
let priceHistory = [];
const maxChartPoints = 100;
let chartCanvas = null;
let chartCtx = null;

// Initialize Chart
function initOverviewChart() {
    chartCanvas = document.getElementById('priceChart');
    if (!chartCanvas) return;
    
    chartCtx = chartCanvas.getContext('2d');
    resizeChart();
    window.addEventListener('resize', resizeChart);
}

function resizeChart() {
    if (!chartCanvas) return;
    chartCanvas.width = chartCanvas.parentElement.clientWidth;
    chartCanvas.height = 250;
    drawOverviewChart();
}

function drawOverviewChart() {
    if (!chartCtx || priceHistory.length < 2) return;
    
    chartCtx.clearRect(0, 0, chartCanvas.width, chartCanvas.height);
    
    const min = Math.min(...priceHistory);
    const max = Math.max(...priceHistory);
    const range = max - min || 1;
    
    // Draw grid lines
    chartCtx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
    chartCtx.lineWidth = 1;
    for (let i = 1; i < 5; i++) {
        const y = (chartCanvas.height / 5) * i;
        chartCtx.beginPath();
        chartCtx.moveTo(0, y);
        chartCtx.lineTo(chartCanvas.width, y);
        chartCtx.stroke();
    }

    // Draw Price Path
    chartCtx.beginPath();
    chartCtx.strokeStyle = '#3b82f6';
    chartCtx.lineWidth = 3;
    
    const stepX = chartCanvas.width / (maxChartPoints - 1);
    
    priceHistory.forEach((price, idx) => {
        const x = idx * stepX;
        const y = chartCanvas.height - ((price - min) / range * (chartCanvas.height - 60) + 30);
        if (idx === 0) chartCtx.moveTo(x, y);
        else chartCtx.lineTo(x, y);
    });
    
    chartCtx.stroke();
    
    // Gradient fill area under path
    const gradient = chartCtx.createLinearGradient(0, 0, 0, chartCanvas.height);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.15)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0)');
    
    chartCtx.lineTo((priceHistory.length - 1) * stepX, chartCanvas.height);
    chartCtx.lineTo(0, chartCanvas.height);
    chartCtx.fillStyle = gradient;
    chartCtx.fill();
    
    // Draw current price indicator line
    const currentPrice = priceHistory[priceHistory.length - 1];
    const lastY = chartCanvas.height - ((currentPrice - min) / range * (chartCanvas.height - 60) + 30);
    
    chartCtx.strokeStyle = 'rgba(16, 185, 129, 0.4)';
    chartCtx.setLineDash([5, 5]);
    chartCtx.beginPath();
    chartCtx.moveTo(0, lastY);
    chartCtx.lineTo(chartCanvas.width, lastY);
    chartCtx.stroke();
    chartCtx.setLineDash([]);
}

function updatePriceHistory(price) {
    priceHistory.push(price);
    if (priceHistory.length > maxChartPoints) {
        priceHistory.shift();
    }
    drawOverviewChart();
}

// Fetch watchlist symbols from config and render chips
async function loadWatchlist() {
    try {
        const res = await apiFetch('/api/config');
        if (!res || !res.ok) return;
        
        const config = await res.json();
        const symbols = config.symbols.split(',').map(s => s.trim().toUpperCase());
        
        const container = document.getElementById('watchlist-container');
        if (!container) return;
        
        container.innerHTML = '';
        
        // Use first symbol as active default if none is set
        if (!activeSymbol && symbols.length > 0) {
            activeSymbol = symbols[0];
            localStorage.setItem('binbot_active_symbol', activeSymbol);
        }
        
        symbols.forEach(symbol => {
            const chip = document.createElement('div');
            chip.className = `asset-chip ${symbol === activeSymbol ? 'active' : ''}`;
            chip.innerText = symbol;
            chip.onclick = () => selectWatchlistSymbol(symbol);
            container.appendChild(chip);
        });
        
        // Update elements
        const activeAssetLabel = document.getElementById('activeAsset');
        if (activeAssetLabel) activeAssetLabel.innerText = activeSymbol;
        
        // Listen to Socket prices
        if (socket) {
            socket.emit('subscribe_prices', { symbols: [activeSymbol] });
        }
    } catch (e) {
        console.error("Error loading watchlist:", e);
    }
}

function selectWatchlistSymbol(symbol) {
    if (symbol === activeSymbol) return;
    
    // Unsubscribe from old
    if (socket) {
        socket.emit('unsubscribe_prices', { symbols: [activeSymbol] });
    }
    
    activeSymbol = symbol;
    localStorage.setItem('binbot_active_symbol', activeSymbol);
    
    // Update active badge and clear history
    const activeAssetLabel = document.getElementById('activeAsset');
    if (activeAssetLabel) activeAssetLabel.innerText = activeSymbol;
    priceHistory = [];
    
    // Reset price display
    const priceDisplay = document.getElementById('livePrice');
    if (priceDisplay) priceDisplay.innerText = '$0.00';
    
    // Update chips active state
    document.querySelectorAll('.asset-chip').forEach(chip => {
        if (chip.innerText === symbol) chip.classList.add('active');
        else chip.classList.remove('active');
    });
    
    // Subscribe to new
    if (socket) {
        socket.emit('subscribe_prices', { symbols: [activeSymbol] });
    }
    
    showToast(`Chart switched to ${symbol}`, 'info');
}

// Fetch stats for cards
async function loadOverviewStats() {
    try {
        const res = await apiFetch('/api/analytics/overview');
        if (!res || !res.ok) return;
        
        const stats = await res.json();
        
        // Update stats
        document.getElementById('stat-win-rate').innerText = `${stats.win_rate.toFixed(1)}%`;
        
        const pfElem = document.getElementById('stat-profit-factor');
        if (stats.profit_factor !== null) {
            pfElem.innerText = stats.profit_factor.toFixed(2);
        } else {
            pfElem.innerText = 'N/A';
        }
        
        // Total trades or scans fallback
        document.getElementById('stat-total-scans').innerText = stats.total_trades; // or from performance
        document.getElementById('stat-daily-drawdown').innerText = `${stats.max_drawdown.toFixed(2)}%`;
    } catch (e) {
        console.error("Error loading stats:", e);
    }
}

// Fetch active positions
async function loadActivePositions() {
    try {
        const res = await apiFetch('/api/trades/active');
        if (!res || !res.ok) return;
        
        const positions = await res.json();
        const tbody = document.getElementById('active-positions-body');
        if (!tbody) return;
        
        if (positions.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                        No active positions. The AI scanner is looking for entry signals...
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = '';
        positions.forEach(pos => {
            const tr = document.createElement('tr');
            const pnlClass = pos.realized_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
            
            tr.innerHTML = `
                <td style="font-weight: 600; color: var(--text-primary);">${pos.symbol}</td>
                <td><span class="badge ${pos.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${pos.side}</span></td>
                <td>${pos.leverage}x</td>
                <td>$${pos.entry_price ? pos.entry_price.toFixed(4) : '---'}</td>
                <td>$${pos.exit_price ? pos.exit_price.toFixed(4) : '---'}</td> <!-- Fallback or mark price -->
                <td>
                    <input type="number" id="tp_${pos.id}" value="${pos.tp1_price || ''}" step="0.0001" style="width: 90px; padding: 2px 4px; font-size: 0.8rem; background: #0f1726; border: 1px solid var(--border-color); color: var(--success);">
                </td>
                <td>
                    <input type="number" id="sl_${pos.id}" value="${pos.sl_price || ''}" step="0.0001" style="width: 90px; padding: 2px 4px; font-size: 0.8rem; background: #0f1726; border: 1px solid var(--border-color); color: var(--danger);">
                </td>
                <td class="${pnlClass}">${pos.realized_pnl >= 0 ? '+' : ''}$${pos.realized_pnl.toFixed(2)}</td>
                <td>
                    <button class="btn btn-primary" onclick="syncProtection('${pos.id}')" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; border-radius: 6px;">Sync</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Error loading active positions:", e);
    }
}

// Sync TP/SL levels to backend
async function syncProtection(tradeId) {
    const tpVal = parseFloat(document.getElementById(`tp_${tradeId}`).value);
    const slVal = parseFloat(document.getElementById(`sl_${tradeId}`).value);
    
    if (isNaN(tpVal) || isNaN(slVal)) {
        showToast("Invalid Take Profit or Stop Loss values", "warning");
        return;
    }
    
    try {
        const res = await apiFetch(`/api/trades/${tradeId}/protection`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tp1_price: tpVal,
                sl_price: slVal
            })
        });
        
        if (res && res.ok) {
            showToast("Protection limits updated successfully", "success");
            loadActivePositions();
        } else {
            const err = await res.json();
            showToast(`Sync failed: ${err.detail || 'unknown error'}`, "error");
        }
    } catch (e) {
        console.error("Error syncing protection:", e);
    }
}

// Load logs initially
async function loadLogs() {
    try {
        const res = await apiFetch('/api/logs');
        if (!res || !res.ok) return;
        
        const logs = await res.json();
        const container = document.getElementById('logsContainer');
        if (!container) return;
        
        container.innerHTML = logs.map(l => `
            <div class="log-entry ${l.level === 'error' ? 'log-error' : (l.level === 'warning' ? 'log-warn' : (l.level === 'success' ? 'log-success' : ''))}">
                <span style="color: #64748b">${new Date(l.timestamp).toLocaleTimeString()}</span>
                <b style="color: ${l.level === 'error' ? '#ef4444' : (l.level === 'warning' ? '#f59e0b' : (l.level === 'success' ? '#22c55e' : '#3b82f6'))}">${l.level.toUpperCase()}</b>
                ${l.message}
            </div>
        `).join('');
    } catch (e) {
        console.error("Error loading logs:", e);
    }
}

// Set up page listeners and socket listeners
function initOverviewPage() {
    initOverviewChart();
    loadWatchlist();
    loadOverviewStats();
    loadActivePositions();
    loadLogs();
    
    // Bind Socket.IO updates
    if (socket) {
        socket.on('price_update', (data) => {
            if (data.symbol === activeSymbol) {
                updatePriceHistory(data.price);
                const priceDisplay = document.getElementById('livePrice');
                if (priceDisplay) priceDisplay.innerText = `$${data.price.toFixed(4)}`;
            }
        });
        
        socket.on('trade_update', (data) => {
            loadActivePositions();
            loadOverviewStats();
        });
    }
    
    // Refresh stats and positions every 15s as fallback
    setInterval(() => {
        loadOverviewStats();
        loadActivePositions();
    }, 15000);
}

// Kickoff on DOM loaded
document.addEventListener('DOMContentLoaded', () => {
    // Wait a brief moment for socket initialization in app.js
    setTimeout(initOverviewPage, 500);
});
