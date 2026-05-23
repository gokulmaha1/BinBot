/**
 * BinBot PRO - AI Auto Mode Page Script
 */

// Load current bot status and update UI elements
async function updateBotStatusUI() {
    try {
        const res = await apiFetch('/api/bot/status');
        if (!res || !res.ok) return;
        
        const data = await res.json();
        
        // Update daily counts
        const tradesTodayElem = document.getElementById('ai-trades-today');
        const consecLossesElem = document.getElementById('ai-consec-losses');
        const modeBadge = document.getElementById('execution-mode');
        
        if (tradesTodayElem) tradesTodayElem.innerText = data.trades_today;
        if (consecLossesElem) consecLossesElem.innerText = data.consecutive_losses;
        if (modeBadge && data.trading_mode) {
            modeBadge.innerText = `${data.trading_mode.toUpperCase()} MODE`;
            if (data.trading_mode === 'live') {
                modeBadge.className = 'badge badge-sell'; // Red/Danger for Live
            } else {
                modeBadge.className = 'badge badge-info'; // Blue for Paper/Testnet
            }
        }
        
        // Update control button highlights
        const startBtn = document.getElementById('btn-start-bot');
        const pauseBtn = document.getElementById('btn-pause-bot');
        const stopBtn = document.getElementById('btn-stop-bot');
        
        if (startBtn && pauseBtn && stopBtn) {
            // Reset shadows and opacities
            startBtn.style.boxShadow = '';
            startBtn.style.opacity = '1';
            pauseBtn.style.boxShadow = '';
            pauseBtn.style.opacity = '1';
            stopBtn.style.boxShadow = '';
            stopBtn.style.opacity = '1';
            
            if (data.status === 'running') {
                startBtn.style.boxShadow = '0 0 15px rgba(16, 185, 129, 0.4)';
                pauseBtn.style.opacity = '0.6';
                stopBtn.style.opacity = '0.6';
            } else if (data.status === 'paused') {
                pauseBtn.style.boxShadow = '0 0 15px rgba(245, 158, 11, 0.4)';
                startBtn.style.opacity = '0.6';
                stopBtn.style.opacity = '0.6';
            } else {
                stopBtn.style.boxShadow = '0 0 15px rgba(244, 63, 94, 0.4)';
                startBtn.style.opacity = '0.6';
                pauseBtn.style.opacity = '0.6';
            }
        }
        
        // Update top header status
        updateBotStatusBadge(data.status.toUpperCase());
    } catch (e) {
        console.error("Error loading bot status:", e);
    }
}

// Bot Control Actions
async function startBotEngine() {
    try {
        const res = await apiFetch('/api/bot/start', { method: 'POST' });
        if (res && res.ok) {
            const data = await res.json();
            showToast(data.message, 'success');
            updateBotStatusUI();
            updateHeaderStats();
        } else {
            const err = await res.json();
            showToast(`Start failed: ${err.detail || 'unknown error'}`, 'error');
        }
    } catch (e) {
        console.error("Error starting bot:", e);
    }
}

async function pauseBotEngine() {
    try {
        const res = await apiFetch('/api/bot/pause', { method: 'POST' });
        if (res && res.ok) {
            const data = await res.json();
            showToast(data.message, 'warning');
            updateBotStatusUI();
            updateHeaderStats();
        } else {
            const err = await res.json();
            showToast(`Pause failed: ${err.detail || 'unknown error'}`, 'error');
        }
    } catch (e) {
        console.error("Error pausing bot:", e);
    }
}

async function stopBotEngine() {
    try {
        const res = await apiFetch('/api/bot/stop', { method: 'POST' });
        if (res && res.ok) {
            const data = await res.json();
            showToast(data.message, 'info');
            updateBotStatusUI();
            updateHeaderStats();
        } else {
            const err = await res.json();
            showToast(`Stop failed: ${err.detail || 'unknown error'}`, 'error');
        }
    } catch (e) {
        console.error("Error stopping bot:", e);
    }
}

async function resetDailyCounters() {
    if (!confirm("Are you sure you want to reset daily PnL and consecutive loss counts?")) return;
    
    try {
        const res = await apiFetch('/api/bot/reset_daily', { method: 'POST' });
        if (res && res.ok) {
            const data = await res.json();
            showToast(data.message, 'success');
            updateBotStatusUI();
            updateHeaderStats();
        } else {
            const err = await res.json();
            showToast(`Reset failed: ${err.detail || 'unknown error'}`, 'error');
        }
    } catch (e) {
        console.error("Error resetting counters:", e);
    }
}

// Initial logs fetch
async function loadLogs() {
    try {
        const res = await apiFetch('/api/logs');
        if (!res || !res.ok) return;
        
        const logs = await res.json();
        const container = document.getElementById('logsContainer');
        if (!container) return;
        
        container.innerHTML = logs.map(l => {
            const levelClass = l.level === 'error' ? 'error' : (l.level === 'warning' ? 'warning' : (l.level === 'trade' ? 'success' : 'info'));
            const levelColor = l.level === 'error' ? '#ef4444' : (l.level === 'warning' ? '#f59e0b' : (l.level === 'trade' ? '#22c55e' : '#3b82f6'));
            return `
                <div class="log-line ${levelClass}">
                    <span style="color: #64748b">${new Date(l.timestamp).toLocaleTimeString()}</span>
                    <b style="color: ${levelColor}">${l.level.toUpperCase()}</b>
                    ${l.message}
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error("Error loading logs:", e);
    }
}

// Load Risk Config
async function loadRiskConfig() {
    try {
        const res = await apiFetch('/api/config');
        if (!res || !res.ok) return;
        
        const data = await res.json();
        const marginEl = document.getElementById('cfg-margin');
        const leverageEl = document.getElementById('cfg-leverage');
        const tpEl = document.getElementById('cfg-tp');
        const slEl = document.getElementById('cfg-sl');
        
        if (marginEl) marginEl.value = Math.round(data.capital_per_trade_pct * 100);
        if (leverageEl) leverageEl.value = data.max_leverage;
        if (tpEl) tpEl.value = data.tp1_ratio;
        if (slEl) slEl.value = Math.round(data.max_risk_per_trade * 100);
    } catch (e) {
        console.error("Error loading risk config:", e);
    }
}

// Save Risk Config
async function saveRiskConfig(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-save-config');
    btn.disabled = true;
    btn.innerText = 'Saving...';
    
    const margin = parseInt(document.getElementById('cfg-margin').value) / 100;
    const leverage = parseInt(document.getElementById('cfg-leverage').value);
    const tp = parseFloat(document.getElementById('cfg-tp').value);
    const sl = parseInt(document.getElementById('cfg-sl').value) / 100;
    
    try {
        const res = await apiFetch('/api/config', {
            method: 'PUT',
            body: JSON.stringify({
                capital_per_trade_pct: margin,
                max_leverage: leverage,
                tp1_ratio: tp,
                max_risk_per_trade: sl
            })
        });
        
        if (res && res.ok) {
            showToast('Risk configuration saved and applied!', 'success');
        } else {
            const err = await res.json();
            showToast(`Save failed: ${err.detail || 'unknown error'}`, 'error');
        }
    } catch (err) {
        console.error("Error saving risk config:", err);
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save Configuration';
    }
}

// Bind button clicks and Socket events
function initAIPage() {
    updateBotStatusUI();
    loadLogs();
    
    // Bind buttons
    const startBtn = document.getElementById('btn-start-bot');
    const pauseBtn = document.getElementById('btn-pause-bot');
    const stopBtn = document.getElementById('btn-stop-bot');
    const resetBtn = document.getElementById('btn-reset-daily');
    const configForm = document.getElementById('risk-config-form');
    
    if (startBtn) startBtn.onclick = startBotEngine;
    if (pauseBtn) pauseBtn.onclick = pauseBotEngine;
    if (stopBtn) stopBtn.onclick = stopBotEngine;
    if (resetBtn) resetBtn.onclick = resetDailyCounters;
    if (configForm) configForm.onsubmit = saveRiskConfig;
    
    // Load config on startup
    loadRiskConfig();
    
    // Socket hooks
    if (socket) {
        socket.on('bot_status', (data) => {
            updateBotStatusUI();
            updateHeaderStats();
        });
    }
    
    // Refresh status loop as fallback
    setInterval(updateBotStatusUI, 10000);
}

// Kickoff
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initAIPage, 500);
});
