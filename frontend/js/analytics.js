/**
 * BinBot PRO - Analytics Page Script
 */

let equityData = [];
let canvas = null;
let ctx = null;

// Initialize Canvas
function initEquityChart() {
    canvas = document.getElementById('equityChart');
    if (!canvas) return;
    
    ctx = canvas.getContext('2d');
    resizeEquityChart();
    window.addEventListener('resize', resizeEquityChart);
}

function resizeEquityChart() {
    if (!canvas) return;
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = 300;
    drawEquityChart();
}

function drawEquityChart() {
    if (!ctx || equityData.length === 0) return;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Extract dates and values
    const values = equityData.map(d => d.equity);
    const dates = equityData.map(d => d.date);
    
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const valRange = maxVal - minVal || 1;
    
    // Draw vertical/horizontal grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
    ctx.lineWidth = 1;
    for (let i = 1; i < 5; i++) {
        const y = (canvas.height / 5) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(canvas.width, y);
        ctx.stroke();
    }
    
    // Draw Equity Path
    ctx.beginPath();
    ctx.strokeStyle = '#10b981'; // Green for growth
    ctx.lineWidth = 3;
    
    const stepX = canvas.width / (equityData.length - 1 || 1);
    
    values.forEach((val, idx) => {
        const x = idx * stepX;
        const y = canvas.height - ((val - minVal) / valRange * (canvas.height - 80) + 40);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    
    ctx.stroke();
    
    // Draw Gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
    gradient.addColorStop(0, 'rgba(16, 185, 129, 0.15)');
    gradient.addColorStop(1, 'rgba(16, 185, 129, 0)');
    ctx.lineTo((values.length - 1) * stepX, canvas.height);
    ctx.lineTo(0, canvas.height);
    ctx.fillStyle = gradient;
    ctx.fill();
    
    // Draw text overlays for start/end points
    if (equityData.length > 0) {
        ctx.fillStyle = '#f8fafc';
        ctx.font = '10px monospace';
        
        // Start point
        const startVal = values[0];
        const startY = canvas.height - ((startVal - minVal) / valRange * (canvas.height - 80) + 40);
        ctx.fillText(`Start: $${startVal.toFixed(2)}`, 10, startY - 10);
        
        // End point
        const endVal = values[values.length - 1];
        const endY = canvas.height - ((endVal - minVal) / valRange * (canvas.height - 80) + 40);
        ctx.textAlign = 'right';
        ctx.fillText(`Current: $${endVal.toFixed(2)}`, canvas.width - 10, endY - 10);
    }
}

// Fetch analytics files
async function loadAnalyticsData() {
    try {
        // 1. Load Equity curve
        const eqRes = await apiFetch('/api/analytics/equity');
        if (eqRes && eqRes.ok) {
            equityData = await eqRes.json();
            
            // If empty, mock standard starting balance
            if (equityData.length === 0) {
                equityData = [
                    { date: 'Initial', equity: 1000.0 },
                    { date: 'Current', equity: 1000.0 }
                ];
            }
            drawEquityChart();
        }
        
        // 2. Load Strategies performance
        const stratRes = await apiFetch('/api/analytics/strategies');
        if (stratRes && stratRes.ok) {
            const strats = await stratRes.json();
            renderStrategiesTable(strats);
        }
        
        // 3. Load Daily operations
        const dailyRes = await apiFetch('/api/analytics/daily?days=30');
        if (dailyRes && dailyRes.ok) {
            const daily = await dailyRes.json();
            renderDailyTable(daily);
        }
    } catch (e) {
        console.error("Error loading analytics data:", e);
    }
}

function renderStrategiesTable(strategies) {
    const tbody = document.getElementById('strategies-table-body');
    if (!tbody) return;
    
    if (strategies.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                    No strategies efficacy data available.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    strategies.forEach(s => {
        const tr = document.createElement('tr');
        const pnl = s.total_pnl || 0;
        const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        
        tr.innerHTML = `
            <td style="font-weight: 600; color: var(--text-primary);">${s.name}</td>
            <td>${s.total_trades}</td>
            <td>${s.win_rate.toFixed(1)}%</td>
            <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderDailyTable(daily) {
    const tbody = document.getElementById('daily-table-body');
    if (!tbody) return;
    
    if (daily.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                    No daily historical metrics logged.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    
    // Sort in reverse order (newest first)
    const reversedDaily = [...daily].reverse();
    
    reversedDaily.forEach(d => {
        const tr = document.createElement('tr');
        const pnlClass = d.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        const dateFormatted = new Date(d.date).toLocaleDateString();
        
        tr.innerHTML = `
            <td style="font-weight: 600;">${dateFormatted}</td>
            <td class="${pnlClass}">${d.pnl >= 0 ? '+' : ''}$${d.pnl.toFixed(2)}</td>
            <td class="${pnlClass}">${d.pnl_pct >= 0 ? '+' : ''}${d.pnl_pct.toFixed(2)}%</td>
            <td>${d.trades}</td>
            <td>${d.win_rate.toFixed(1)}%</td>
            <td class="pnl-neg">${d.drawdown.toFixed(2)}%</td>
        `;
        tbody.appendChild(tr);
    });
}

function initAnalyticsPage() {
    initEquityChart();
    loadAnalyticsData();
}

// Dom Loaded
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initAnalyticsPage, 500);
});
