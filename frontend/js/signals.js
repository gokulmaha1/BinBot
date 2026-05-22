/**
 * BinBot PRO - Signals Page Script
 */

let currentPage = 1;
const pageSize = 20;
let totalPages = 1;
let loadedSignals = []; // Local cache of current page signals

// Fetch signals list from API
async function loadSignals() {
    const symbolVal = document.getElementById('sig-filter-symbol').value.trim();
    const statusVal = document.getElementById('sig-filter-status').value;
    const scoreVal = document.getElementById('sig-filter-score').value;
    
    let url = `/api/signals?page=${currentPage}&page_size=${pageSize}`;
    if (symbolVal) url += `&symbol=${encodeURIComponent(symbolVal)}`;
    if (statusVal) url += `&status=${encodeURIComponent(statusVal)}`;
    if (scoreVal) url += `&min_score=${encodeURIComponent(scoreVal)}`;
    
    try {
        const res = await apiFetch(url);
        if (!res || !res.ok) return;
        
        const data = await res.json();
        loadedSignals = data.signals;
        
        renderSignalsTable(loadedSignals);
        
        // Update pagination
        totalPages = Math.ceil(data.total / pageSize) || 1;
        document.getElementById('sig-pagination-info').innerText = `Showing page ${currentPage} of ${totalPages} (${data.total} total signals)`;
        document.getElementById('signal-count-badge').innerText = `${data.total} Signals`;
        
        document.getElementById('btn-sig-prev').disabled = currentPage <= 1;
        document.getElementById('btn-sig-next').disabled = currentPage >= totalPages;
    } catch (e) {
        console.error("Error loading signals list:", e);
    }
}

// Render signals list
function renderSignalsTable(signals) {
    const tbody = document.getElementById('signals-table-body');
    if (!tbody) return;
    
    if (signals.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 3rem;">
                    No signals matching filters.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    signals.forEach((sig, idx) => {
        const tr = document.createElement('tr');
        
        const timeFormatted = sig.created_at ? new Date(sig.created_at).toLocaleString() : '---';
        const scoreClass = sig.score >= 80 ? 'pass' : 'fail';
        
        let statusBadge = 'badge-info';
        if (sig.status === 'executed') statusBadge = 'badge-buy';
        if (sig.status === 'rejected') statusBadge = 'badge-sell';
        
        const reason = sig.reject_reason || (sig.status === 'executed' ? 'Trade placed on exchange' : 'N/A');
        
        tr.innerHTML = `
            <td>${timeFormatted}</td>
            <td style="font-weight: 700; color: var(--text-primary);">${sig.symbol}</td>
            <td><span class="badge ${sig.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${sig.side}</span></td>
            <td style="color: var(--text-primary);">${sig.strategy_name}</td>
            <td>${sig.regime ? sig.regime.toUpperCase() : 'UNKNOWN'}</td>
            <td>
                <div class="score-circle ${scoreClass}">
                    ${sig.score.toFixed(0)}
                </div>
            </td>
            <td style="font-weight: 600; color: var(--primary);">${(sig.ml_confidence * 100).toFixed(1)}%</td>
            <td><span class="badge ${statusBadge}">${sig.status}</span></td>
            <td style="font-size: 0.8rem; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${reason}">
                ${reason}
            </td>
            <td>
                <button class="btn btn-secondary" onclick="openSignalDetails(${idx})" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; border-radius: 6px;">
                    📊 Details
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// Open modal and render factors
function openSignalDetails(index) {
    const sig = loadedSignals[index];
    if (!sig) return;
    
    document.getElementById('modal-title-symbol').innerText = `${sig.symbol} (${sig.side})`;
    
    const scoreElem = document.getElementById('modal-score');
    scoreElem.innerText = sig.score.toFixed(0);
    scoreElem.className = sig.score >= 80 ? 'pnl-pos' : 'pnl-neg';
    
    const mlElem = document.getElementById('modal-ml-conf');
    mlElem.innerText = `${(sig.ml_confidence * 100).toFixed(1)}%`;
    mlElem.className = sig.ml_confidence >= 0.75 ? 'pnl-pos' : 'pnl-neg';
    
    const container = document.getElementById('modal-factors-container');
    container.innerHTML = '';
    
    const breakdown = sig.score_breakdown || {};
    
    // Humanized factor names mapping
    const factorLabels = {
        trend_alignment: "Trend Alignment (30%)",
        momentum: "Momentum Confirmation (20%)",
        volume: "Volume Analysis (15%)",
        volatility: "Volatility Threshold (15%)",
        market_structure: "Market Structure (10%)",
        order_flow: "Order Flow Imbalance (10%)"
    };
    
    Object.keys(factorLabels).forEach(key => {
        const scoreVal = breakdown[key] || 0;
        const div = document.createElement('div');
        div.className = 'factor-bar-container';
        
        div.innerHTML = `
            <div class="factor-label">
                <span>${factorLabels[key]}</span>
                <span style="font-weight:600; color:var(--text-primary);">${(scoreVal * 100).toFixed(0)}%</span>
            </div>
            <div class="factor-bg">
                <div class="factor-fill" style="width: ${scoreVal * 100}%; background-color: ${scoreVal >= 0.8 ? 'var(--success)' : (scoreVal >= 0.5 ? 'var(--primary)' : 'var(--danger)')}"></div>
            </div>
        `;
        container.appendChild(div);
    });
    
    document.getElementById('signal-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('signal-modal').classList.remove('active');
}

// Initialise Page
function initSignalsPage() {
    loadSignals();
    
    // Wire apply
    const applyBtn = document.getElementById('btn-sig-apply');
    if (applyBtn) {
        applyBtn.onclick = () => {
            currentPage = 1;
            loadSignals();
            showToast("Filters applied", "info");
        };
    }
    
    // Wire clear
    const clearBtn = document.getElementById('btn-sig-clear');
    if (clearBtn) {
        clearBtn.onclick = () => {
            document.getElementById('sig-filter-symbol').value = '';
            document.getElementById('sig-filter-status').value = '';
            document.getElementById('sig-filter-score').value = '';
            currentPage = 1;
            loadSignals();
            showToast("Filters cleared", "info");
        };
    }
    
    // Wire prev
    const prevBtn = document.getElementById('btn-sig-prev');
    if (prevBtn) {
        prevBtn.onclick = () => {
            if (currentPage > 1) {
                currentPage--;
                loadSignals();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        };
    }
    
    // Wire next
    const nextBtn = document.getElementById('btn-sig-next');
    if (nextBtn) {
        nextBtn.onclick = () => {
            if (currentPage < totalPages) {
                currentPage++;
                loadSignals();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        };
    }
    
    // Wire Socket
    if (socket) {
        socket.on('signal_update', (data) => {
            loadSignals();
            showToast(`New signal generated for ${data.symbol}`, "info");
        });
    }
    
    // Fallback poll
    setInterval(loadSignals, 30000);
}

// Dom Loaded
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initSignalsPage, 500);
});
