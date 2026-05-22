let activeChips = [];

/**
 * Helper to parse, validate and add symbols to activeChips
 */
function addSymbolsFromInput(value) {
    if (!value) return;
    const symbols = value.split(/[\s,]+/).map(s => s.trim().toUpperCase().replace(/[^A-Z0-9]/g, '')).filter(s => s.length > 0);
    let added = false;
    symbols.forEach(sym => {
        if (!activeChips.includes(sym)) {
            activeChips.push(sym);
            added = true;
        }
    });
    if (added) {
        renderChips();
    }
}

/**
 * Render active chips inside the chips container
 */
function renderChips() {
    const container = document.getElementById('chips-container');
    if (!container) return;
    
    // Remove existing chip elements
    const existingChips = container.querySelectorAll('.chip');
    existingChips.forEach(c => c.remove());
    
    const input = document.getElementById('cfg-manual-pairs-input');
    
    // Create and prepend new chips
    activeChips.forEach(symbol => {
        const chip = document.createElement('div');
        chip.className = 'chip';
        chip.innerHTML = `
            <span>${symbol}</span>
            <span class="chip-remove" onclick="removeChip('${symbol}')">&times;</span>
        `;
        container.insertBefore(chip, input);
    });
}

// Make removeChip global so inline onclick can access it
window.removeChip = function(symbol) {
    activeChips = activeChips.filter(s => s !== symbol);
    renderChips();
};

document.addEventListener('DOMContentLoaded', () => {
    // 1. Fetch and render bot configuration
    loadConfig();

    // 2. Fetch and render active exchange accounts
    loadExchangeAccounts();

    // 3. Bind save config event listener
    const btnSaveParams = document.getElementById('btn-save-params');
    if (btnSaveParams) {
        btnSaveParams.addEventListener('click', saveConfig);
    }

    // 4. Bind add exchange account event listener
    const btnAddExchange = document.getElementById('btn-add-exchange');
    if (btnAddExchange) {
        btnAddExchange.addEventListener('click', addExchangeAccount);
    }

    // 5. Initialize Chips Input Listeners
    const container = document.getElementById('chips-container');
    const input = document.getElementById('cfg-manual-pairs-input');
    if (container && input) {
        container.addEventListener('click', (e) => {
            if (e.target === container || e.target.classList.contains('chips-container')) {
                input.focus();
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault();
                addSymbolsFromInput(input.value);
                input.value = '';
            } else if (e.key === 'Backspace' && input.value === '' && activeChips.length > 0) {
                activeChips.pop();
                renderChips();
            }
        });

        input.addEventListener('paste', (e) => {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text');
            addSymbolsFromInput(text);
        });

        input.addEventListener('blur', () => {
            addSymbolsFromInput(input.value);
            input.value = '';
        });
    }
});

/**
 * Fetch config settings from API and pre-fill form fields
 */
async function loadConfig() {
    try {
        const res = await apiFetch('/api/config');
        if (res && res.ok) {
            const config = await res.json();
            
            // Populate inputs
            const capInput = document.getElementById('cfg-capital-pct');
            if (capInput) capInput.value = Math.round(config.capital_per_trade_pct * 100);

            const modeSelect = document.getElementById('cfg-trading-mode');
            if (modeSelect && config.trading_mode) modeSelect.value = config.trading_mode;

            const tp1Ratio = document.getElementById('cfg-tp1-ratio');
            const tp1Close = document.getElementById('cfg-tp1-close');
            if (tp1Ratio) tp1Ratio.value = config.tp1_ratio;
            if (tp1Close) tp1Close.value = Math.round(config.tp1_close_pct * 100);

            const tp2Ratio = document.getElementById('cfg-tp2-ratio');
            const tp2Close = document.getElementById('cfg-tp2-close');
            if (tp2Ratio) tp2Ratio.value = config.tp2_ratio;
            if (tp2Close) tp2Close.value = Math.round(config.tp2_close_pct * 100);

            const tp3Ratio = document.getElementById('cfg-tp3-ratio');
            const tp3Close = document.getElementById('cfg-tp3-close');
            if (tp3Ratio) tp3Ratio.value = config.tp3_ratio;
            if (tp3Close) tp3Close.value = Math.round(config.tp3_close_pct * 100);

            const scanVolume = document.getElementById('cfg-scan-volume');
            const scanTop = document.getElementById('cfg-scan-top');
            if (scanVolume) scanVolume.value = config.scanner_min_volume_24h;
            if (scanTop) scanTop.value = config.scanner_top_pairs;

            if (config.scanner_manual_pairs) {
                activeChips = config.scanner_manual_pairs.split(',').map(s => s.trim().toUpperCase()).filter(s => s.length > 0);
            } else {
                activeChips = [];
            }
            renderChips();

            // Populate Read-Only Hard Limits
            const roMaxLev = document.getElementById('ro-max-lev');
            if (roMaxLev) roMaxLev.innerText = `${config.max_leverage}x`;

            const roMaxPos = document.getElementById('ro-max-pos');
            if (roMaxPos) roMaxPos.innerText = config.max_active_positions;

            const roDailyLoss = document.getElementById('ro-daily-loss');
            if (roDailyLoss) roDailyLoss.innerText = `${(config.max_daily_loss * 100).toFixed(2)}%`;

            const roDrawdown = document.getElementById('ro-drawdown');
            if (roDrawdown) roDrawdown.innerText = `${(config.max_drawdown * 100).toFixed(2)}%`;
        } else {
            showToast("Failed to load bot configurations", "error");
        }
    } catch (err) {
        console.error("Error loading config:", err);
        showToast("Error loading bot configurations", "error");
    }
}

/**
 * Save modifiable parameters to config API
 */
async function saveConfig() {
    const btn = document.getElementById('btn-save-params');
    const originalText = btn.innerText;
    
    try {
        const capitalPctInput = document.getElementById('cfg-capital-pct');
        const modeSelect = document.getElementById('cfg-trading-mode');
        const tp1RatioInput = document.getElementById('cfg-tp1-ratio');
        const tp1CloseInput = document.getElementById('cfg-tp1-close');
        const tp2RatioInput = document.getElementById('cfg-tp2-ratio');
        const tp2CloseInput = document.getElementById('cfg-tp2-close');
        const tp3RatioInput = document.getElementById('cfg-tp3-ratio');
        const tp3CloseInput = document.getElementById('cfg-tp3-close');
        const scanVolumeInput = document.getElementById('cfg-scan-volume');
        const scanTopInput = document.getElementById('cfg-scan-top');

        const capitalPct = parseFloat(capitalPctInput ? capitalPctInput.value : '');
        const tradingMode = modeSelect ? modeSelect.value : 'paper';
        const tp1Ratio = parseFloat(tp1RatioInput ? tp1RatioInput.value : '');
        const tp1Close = parseFloat(tp1CloseInput ? tp1CloseInput.value : '');
        const tp2Ratio = parseFloat(tp2RatioInput ? tp2RatioInput.value : '');
        const tp2Close = parseFloat(tp2CloseInput ? tp2CloseInput.value : '');
        const tp3Ratio = parseFloat(tp3RatioInput ? tp3RatioInput.value : '');
        const tp3Close = parseFloat(tp3CloseInput ? tp3CloseInput.value : '');
        const scanVolume = parseFloat(scanVolumeInput ? scanVolumeInput.value : '');
        const scanTop = parseInt(scanTopInput ? scanTopInput.value : '');

        if (isNaN(capitalPct) || capitalPct < 1 || capitalPct > 50) {
            showToast("Capital allocation must be between 1% and 50%", "error");
            return;
        }

        if (isNaN(tp1Ratio) || isNaN(tp1Close) || isNaN(tp2Ratio) || isNaN(tp2Close) || isNaN(tp3Ratio) || isNaN(tp3Close)) {
            showToast("All Take Profit fields must be valid numbers", "error");
            return;
        }

        if (tp1Close + tp2Close + tp3Close !== 100) {
            showToast("Take Profit close percentages must sum to exactly 100%", "error");
            return;
        }

        if (isNaN(scanVolume) || scanVolume < 0) {
            showToast("Min scan volume must be a positive number", "error");
            return;
        }

        if (isNaN(scanTop) || scanTop < 5 || scanTop > 50) {
            showToast("Scanner top pairs limit must be between 5 and 50", "error");
            return;
        }

        if (tradingMode === 'live') {
            const confirmLive = confirm("⚠️ WARNING: You are switching the active trading mode to LIVE. Real funds will be risked. Are you sure you want to proceed?");
            if (!confirmLive) return;
        }

        btn.disabled = true;
        btn.innerText = "⏳ Saving...";

        const payload = {
            capital_per_trade_pct: capitalPct / 100,
            trading_mode: tradingMode,
            tp1_ratio: tp1Ratio,
            tp1_close_pct: tp1Close / 100,
            tp2_ratio: tp2Ratio,
            tp2_close_pct: tp2Close / 100,
            tp3_ratio: tp3Ratio,
            tp3_close_pct: tp3Close / 100,
            scanner_min_volume_24h: scanVolume,
            scanner_top_pairs: scanTop,
            scanner_manual_pairs: activeChips.join(',')
        };

        const res = await apiFetch('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res && res.ok) {
            showToast("Configuration saved successfully", "success");
            await loadConfig(); // Refresh parameters
        } else {
            const errData = await res.json();
            showToast(errData.detail || "Failed to update configuration", "error");
        }
    } catch (err) {
        console.error("Error saving config:", err);
        showToast("Error updating configuration", "error");
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
    }
}

/**
 * Fetch and list all active exchange accounts
 */
async function loadExchangeAccounts() {
    try {
        const res = await apiFetch('/api/config/exchange');
        if (res && res.ok) {
            const accounts = await res.json();
            renderAccountsTable(accounts);
        } else {
            showToast("Failed to load exchange accounts", "error");
        }
    } catch (err) {
        console.error("Error loading exchange accounts:", err);
        showToast("Error loading exchange accounts", "error");
    }
}

/**
 * Render the exchange accounts into the HTML table
 */
function renderAccountsTable(accounts) {
    const tbody = document.getElementById('accounts-table-body');
    if (!tbody) return;

    tbody.innerHTML = '';
    if (accounts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">
                    No active API links.
                </td>
            </tr>
        `;
        return;
    }

    accounts.forEach(acc => {
        const tr = document.createElement('tr');

        // Style the mode badge
        let badgeClass = 'badge-info';
        if (acc.mode === 'live') {
            badgeClass = 'badge-sell'; // Uses danger styling
        } else if (acc.mode === 'testnet') {
            badgeClass = 'badge-buy'; // Uses success styling
        }

        // Active/inactive status indicator
        const statusHtml = acc.is_active 
            ? `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color:var(--success); margin-right:5px;"></span> Active`
            : `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color:var(--text-muted); margin-right:5px;"></span> Inactive`;

        tr.innerHTML = `
            <td>${acc.exchange.toUpperCase()}</td>
            <td><code>${acc.api_key_preview}</code></td>
            <td><span class="badge ${badgeClass}">${acc.mode.toUpperCase()}</span></td>
            <td>${statusHtml}</td>
        `;
        tbody.appendChild(tr);
    });
}

/**
 * Add a new exchange account (API Key & Secret)
 */
async function addExchangeAccount() {
    const btn = document.getElementById('btn-add-exchange');
    const originalText = btn.innerText;

    try {
        const apiKeyInput = document.getElementById('ex-api-key');
        const apiSecretInput = document.getElementById('ex-api-secret');
        const modeInput = document.getElementById('ex-mode');

        const apiKey = apiKeyInput ? apiKeyInput.value.trim() : '';
        const apiSecret = apiSecretInput ? apiSecretInput.value.trim() : '';
        const mode = modeInput ? modeInput.value : 'testnet';

        if (!apiKey || !apiSecret) {
            showToast("Both API Key and API Secret are required", "error");
            return;
        }

        // Live account confirmation warning
        if (mode === 'live') {
            const confirmLive = confirm("⚠️ WARNING: You are linking a LIVE trading account. Real funds will be risked. Are you sure you want to proceed?");
            if (!confirmLive) return;
        }

        btn.disabled = true;
        btn.innerText = "🔑 Linking Account...";

        const payload = {
            api_key: apiKey,
            api_secret: apiSecret,
            mode: mode
        };

        const res = await apiFetch('/api/config/exchange', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res && res.ok) {
            showToast("API Credentials linked successfully", "success");
            
            // Clear inputs
            if (apiKeyInput) apiKeyInput.value = '';
            if (apiSecretInput) apiSecretInput.value = '';
            
            // Reload accounts list
            await loadExchangeAccounts();
        } else {
            const errData = await res.json();
            showToast(errData.detail || "Failed to link exchange credentials", "error");
        }
    } catch (err) {
        console.error("Error linking exchange credentials:", err);
        showToast("Error linking exchange credentials", "error");
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
    }
}
