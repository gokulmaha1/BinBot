/**
 * BinBot PRO - Positions Management Page Script
 */

let activePositions = [];

// Load active positions from API
async function loadActivePositions() {
    try {
        const res = await apiFetch('/api/trades/active');
        if (!res || !res.ok) return;
        
        activePositions = await res.json();
        renderPositions();
        
        // Subscribe to prices for all active symbols
        if (socket && activePositions.length > 0) {
            const symbols = activePositions.map(pos => pos.symbol);
            socket.emit('subscribe_prices', { symbols });
        }
    } catch (e) {
        console.error("Error loading active positions:", e);
    }
}

// Render positions to UI
function renderPositions() {
    const container = document.getElementById('positions-list-container');
    if (!container) return;
    
    if (activePositions.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; color: var(--text-muted); padding: 3rem;">
                No active positions. The AI auto mode is currently scanning for opportunities...
            </div>
        `;
        return;
    }
    
    container.innerHTML = '';
    
    activePositions.forEach(pos => {
        const card = document.createElement('div');
        const sideClass = pos.side.toLowerCase() === 'buy' ? 'long' : 'short';
        card.className = `position-card ${sideClass}`;
        
        // Initial estimate or stored pnl
        const pnl = pos.realized_pnl || 0;
        const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        
        const entryTimeFormatted = pos.entry_time ? new Date(pos.entry_time).toLocaleString() : 'Pending';
        
        card.innerHTML = `
            <!-- Left: Symbol & Side -->
            <div class="pos-info-block">
                <div class="pos-symbol">
                    ${pos.symbol}
                    <span class="badge ${pos.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${pos.side === 'BUY' ? 'LONG' : 'SHORT'}</span>
                </div>
                <div class="lbl" style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">
                    Opened: ${entryTimeFormatted}
                </div>
                <div class="lbl" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.5rem;">
                    Strategy: <span style="color: var(--primary); font-weight: 600;">${pos.strategy_name}</span>
                </div>
            </div>
            
            <!-- Middle: Details & Inputs -->
            <div class="pos-info-block" style="gap: 1rem;">
                <div class="pos-details-grid">
                    <div class="pos-info-block">
                        <span class="lbl" style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">Leverage</span>
                        <span style="font-weight: 600;">${pos.leverage}x</span>
                    </div>
                    <div class="pos-info-block">
                        <span class="lbl" style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">Quantity</span>
                        <span style="font-weight: 600;">${pos.quantity}</span>
                    </div>
                    <div class="pos-info-block">
                        <span class="lbl" style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">Entry Price</span>
                        <span style="font-weight: 600;">$${pos.entry_price ? pos.entry_price.toFixed(4) : '---'}</span>
                    </div>
                    <div class="pos-info-block">
                        <span class="lbl" style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">Mark Price</span>
                        <span id="mark-price-${pos.id}" style="font-weight: 600; color: var(--primary);">$${pos.entry_price ? pos.entry_price.toFixed(4) : '---'}</span>
                    </div>
                </div>
                
                <div class="protection-input-grid">
                    <div class="protection-input-group">
                        <label for="sl-${pos.id}">Stop Loss (SL)</label>
                        <input type="number" id="sl-${pos.id}" value="${pos.sl_price || ''}" step="0.0001">
                    </div>
                    <div class="protection-input-group">
                        <label for="tp1-${pos.id}">Take Profit 1</label>
                        <input type="number" id="tp1-${pos.id}" value="${pos.tp1_price || ''}" step="0.0001">
                    </div>
                    <div class="protection-input-group">
                        <label for="tp2-${pos.id}">Take Profit 2</label>
                        <input type="number" id="tp2-${pos.id}" value="${pos.tp2_price || ''}" step="0.0001">
                    </div>
                    <div class="protection-input-group">
                        <label for="tp3-${pos.id}">Take Profit 3</label>
                        <input type="number" id="tp3-${pos.id}" value="${pos.tp3_price || ''}" step="0.0001">
                    </div>
                </div>
            </div>
            
            <!-- Right: PnL & Action -->
            <div class="pos-pnl-block">
                <div class="lbl" style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.25rem;">Unrealized PnL</div>
                <div id="pnl-${pos.id}" class="pos-pnl-val ${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                
                <button class="btn btn-primary" onclick="syncProtection('${pos.id}')" style="margin-top: 1rem; width: 100%; font-size: 0.8rem; padding: 0.5rem;">
                    Sync Constraints
                </button>
            </div>
        `;
        
        container.appendChild(card);
    });
}

// Update floating prices and recalculate PnL
function handlePriceUpdate(data) {
    activePositions.forEach(pos => {
        if (pos.symbol === data.symbol) {
            const markPriceElem = document.getElementById(`mark-price-${pos.id}`);
            const pnlElem = document.getElementById(`pnl-${pos.id}`);
            
            if (markPriceElem) markPriceElem.innerText = `$${data.price.toFixed(4)}`;
            
            if (pnlElem && pos.entry_price) {
                // Calculate PnL: Long: (mark - entry) * qty, Short: (entry - mark) * qty
                let diff = data.price - pos.entry_price;
                if (pos.side.toUpperCase() === 'SELL') {
                    diff = pos.entry_price - data.price;
                }
                
                const unrealizedPnL = diff * pos.quantity;
                pnlElem.innerText = `${unrealizedPnL >= 0 ? '+' : ''}$${unrealizedPnL.toFixed(2)}`;
                pnlElem.className = `pos-pnl-val ${unrealizedPnL >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
            }
        }
    });
}

// Sync TP/SL protection modifications
async function syncProtection(tradeId) {
    const slVal = parseFloat(document.getElementById(`sl-${tradeId}`).value);
    const tp1Val = parseFloat(document.getElementById(`tp1-${tradeId}`).value);
    const tp2Val = parseFloat(document.getElementById(`tp2-${tradeId}`).value);
    const tp3Val = parseFloat(document.getElementById(`tp3-${tradeId}`).value);
    
    if (isNaN(slVal)) {
        showToast("Stop Loss is required and must be a number", "warning");
        return;
    }
    
    const body = {
        sl_price: slVal,
        tp1_price: isNaN(tp1Val) ? null : tp1Val,
        tp2_price: isNaN(tp2Val) ? null : tp2Val,
        tp3_price: isNaN(tp3Val) ? null : tp3Val
    };
    
    try {
        const res = await apiFetch(`/api/trades/${tradeId}/protection`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        
        if (res && res.ok) {
            showToast("Position protection levels synced successfully", "success");
            loadActivePositions();
        } else {
            const err = await res.json();
            showToast(`Sync failed: ${err.detail || 'unknown error'}`, "error");
        }
    } catch (e) {
        console.error("Error syncing protection:", e);
    }
}

// Initialization
function initPositionsPage() {
    loadActivePositions();
    
    const refreshBtn = document.getElementById('btn-refresh-positions');
    if (refreshBtn) {
        refreshBtn.onclick = () => {
            loadActivePositions();
            showToast("Positions list updated", "info");
        };
    }
    
    // Bind socket events
    if (socket) {
        socket.on('price_update', handlePriceUpdate);
        socket.on('trade_update', (data) => {
            loadActivePositions();
        });
    }
    
    // Fallback poll
    setInterval(loadActivePositions, 15000);
}

// Dom Loaded
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initPositionsPage, 500);
});
