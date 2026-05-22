/**
 * BinBot PRO - Market Scanner Page Script
 */

// Load ranked pairs from backend
async function loadRankedPairs() {
    try {
        const res = await apiFetch('/api/scanner/ranked');
        if (!res || !res.ok) return;
        
        const pairs = await res.json();
        renderScannerTable(pairs);
    } catch (e) {
        console.error("Error loading scanner data:", e);
    }
}

// Format volume nicely (e.g. $12.34M)
function formatMoney(value) {
    if (value >= 1.0e9) {
        return `$${(value / 1.0e9).toFixed(2)}B`;
    } else if (value >= 1.0e6) {
        return `$${(value / 1.0e6).toFixed(2)}M`;
    } else if (value >= 1.0e3) {
        return `$${(value / 1.0e3).toFixed(2)}K`;
    }
    return `$${value.toFixed(2)}`;
}

// Classify regime badge styles
function getRegimeClass(regime) {
    if (!regime) return 'regime-ranging';
    const r = regime.toLowerCase();
    if (r.includes('bullish')) return 'regime-trending-bullish';
    if (r.includes('bearish')) return 'regime-trending-bearish';
    if (r.includes('breakout')) return 'regime-breakout';
    if (r.includes('reversion')) return 'regime-mean-reversion';
    return 'regime-ranging';
}

function getRegimeLabel(regime) {
    if (!regime) return 'RANGING';
    return regime.replace(/_/g, ' ').toUpperCase();
}

// Render pairs to the table
function renderScannerTable(pairs) {
    const tbody = document.getElementById('scanner-table-body');
    if (!tbody) return;
    
    if (pairs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 3rem;">
                    No scanned markets found. Make sure the AI Engine bot is running.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    pairs.forEach((pair, idx) => {
        const tr = document.createElement('tr');
        
        const timeFormatted = pair.captured_at ? new Date(pair.captured_at).toLocaleTimeString() : '---';
        const scoreClass = pair.scanner_score >= 80 ? 'high' : '';
        const regimeClass = getRegimeClass(pair.regime);
        const regimeLabel = getRegimeLabel(pair.regime);
        
        tr.innerHTML = `
            <td style="font-weight: 700; color: var(--text-primary);">${pair.symbol}</td>
            <td style="color: var(--text-primary); font-family: monospace;">$${pair.price.toFixed(pair.price < 1 ? 4 : 2)}</td>
            <td>${formatMoney(pair.volume_24h)}</td>
            <td>${pair.open_interest ? formatMoney(pair.open_interest) : 'N/A'}</td>
            <td>${pair.atr ? pair.atr.toFixed(2) + '%' : 'N/A'}</td>
            <td>${pair.adx ? pair.adx.toFixed(1) : 'N/A'}</td>
            <td><span class="regime-badge ${regimeClass}">${regimeLabel}</span></td>
            <td><span class="score-badge ${scoreClass}">${pair.scanner_score.toFixed(1)}</span></td>
            <td style="font-size: 0.8rem; color: var(--text-muted);">${timeFormatted}</td>
            <td>
                <button class="btn btn-secondary" onclick="watchSymbol('${pair.symbol}')" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; border-radius: 6px;">
                    👁️ Chart
                </button>
            </td>
        `;
        
        tbody.appendChild(tr);
    });
}

// Set symbol in localstorage and jump to home overview
function watchSymbol(symbol) {
    localStorage.setItem('binbot_active_symbol', symbol);
    showToast(`Redirecting to chart for ${symbol}...`, 'success');
    setTimeout(() => {
        window.location.href = '/dashboard';
    }, 500);
}

// Page entry point
function initScannerPage() {
    loadRankedPairs();
    
    // Connect Socket events
    if (socket) {
        socket.on('scanner_update', (data) => {
            if (data.pairs) {
                renderScannerTable(data.pairs);
                showToast("Scanner list updated with fresh metrics", "info");
                
                const timerElem = document.getElementById('scan-timer');
                if (timerElem) {
                    timerElem.innerText = `Scan Complete at ${new Date().toLocaleTimeString()}`;
                    setTimeout(() => {
                        timerElem.innerText = 'Live Scanning (15s Loop)';
                    }, 5000);
                }
            }
        });
    }
    
    // Poll fallback every 15 seconds
    setInterval(loadRankedPairs, 15000);
}

// Dom Loaded
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initScannerPage, 500);
});
