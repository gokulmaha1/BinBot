/**
 * BinBot PRO - Trade History Page Script
 */

let currentPage = 1;
const pageSize = 20;
let totalPages = 1;

// Load History Stats
async function loadHistoryStats() {
    try {
        const res = await apiFetch('/api/trades/stats?days=30');
        if (!res || !res.ok) return;
        
        const stats = await res.json();
        
        const totalPnLElem = document.getElementById('hist-total-pnl');
        if (totalPnLElem) {
            totalPnLElem.innerText = `${stats.total_pnl >= 0 ? '+' : ''}$${stats.total_pnl.toFixed(2)}`;
            totalPnLElem.className = `stat-number ${stats.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
        }
        
        document.getElementById('hist-win-rate').innerText = `${stats.win_rate.toFixed(1)}%`;
        document.getElementById('hist-win-losing-count').innerText = `${stats.winning_trades} W / ${stats.losing_trades} L`;
        document.getElementById('hist-avg-hold').innerText = `${stats.avg_holding_time_minutes.toFixed(1)} min`;
        
        const bestElem = document.getElementById('hist-best-pnl');
        if (bestElem) {
            bestElem.innerText = `+$${stats.best_trade.toFixed(2)}`;
            bestElem.className = 'stat-number pnl-pos';
        }
        
        const worstElem = document.getElementById('hist-worst-pnl');
        if (worstElem) {
            worstElem.innerText = `-$${Math.abs(stats.worst_trade).toFixed(2)}`;
            worstElem.className = 'stat-number pnl-neg';
        }
    } catch (e) {
        console.error("Error loading history stats:", e);
    }
}

// Load List of Trades
async function loadTrades() {
    const symbolVal = document.getElementById('filter-symbol').value.trim();
    const statusVal = document.getElementById('filter-status').value;
    
    let url = `/api/trades?page=${currentPage}&page_size=${pageSize}`;
    if (symbolVal) url += `&symbol=${encodeURIComponent(symbolVal)}`;
    if (statusVal) url += `&status=${encodeURIComponent(statusVal)}`;
    
    try {
        const res = await apiFetch(url);
        if (!res || !res.ok) return;
        
        const data = await res.json();
        renderTradesTable(data.trades);
        
        // Update pagination
        totalPages = Math.ceil(data.total / pageSize) || 1;
        document.getElementById('pagination-info').innerText = `Showing page ${currentPage} of ${totalPages} (${data.total} total trades)`;
        
        document.getElementById('btn-prev-page').disabled = currentPage <= 1;
        document.getElementById('btn-next-page').disabled = currentPage >= totalPages;
    } catch (e) {
        console.error("Error loading trades list:", e);
    }
}

// Render rows
function renderTradesTable(trades) {
    const tbody = document.getElementById('history-table-body');
    if (!tbody) return;
    
    if (trades.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11" style="text-align: center; color: var(--text-muted); padding: 3rem;">
                    No matching trades found in historical records.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    trades.forEach(t => {
        const tr = document.createElement('tr');
        
        const timeFormatted = t.exit_time ? new Date(t.exit_time).toLocaleString() : (t.entry_time ? new Date(t.entry_time).toLocaleString() : 'Pending');
        const pnl = t.realized_pnl || 0;
        const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        
        tr.innerHTML = `
            <td>${timeFormatted}</td>
            <td style="font-weight: 600; color: var(--text-primary);">${t.symbol}</td>
            <td><span class="badge ${t.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.side}</span></td>
            <td style="color: var(--text-primary);">${t.strategy_name}</td>
            <td>${t.leverage}x</td>
            <td>${t.quantity}</td>
            <td>$${t.entry_price ? t.entry_price.toFixed(4) : '---'}</td>
            <td>$${t.exit_price ? t.exit_price.toFixed(4) : '---'}</td>
            <td>$${t.fees.toFixed(4)}</td>
            <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}</td>
            <td>
                <span class="badge ${t.status === 'CLOSED' ? 'badge-info' : 'badge-buy'}">
                    ${t.close_reason || t.status}
                </span>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// Apply & Clear filters, Prev & Next
function initHistoryPage() {
    loadHistoryStats();
    loadTrades();
    
    // Apply filters click
    const applyBtn = document.getElementById('btn-apply-filters');
    if (applyBtn) {
        applyBtn.onclick = () => {
            currentPage = 1;
            loadTrades();
            showToast("Filters applied", "info");
        };
    }
    
    // Clear filters click
    const clearBtn = document.getElementById('btn-clear-filters');
    if (clearBtn) {
        clearBtn.onclick = () => {
            document.getElementById('filter-symbol').value = '';
            document.getElementById('filter-status').value = 'closed';
            currentPage = 1;
            loadTrades();
            showToast("Filters cleared", "info");
        };
    }
    
    // Previous Page click
    const prevBtn = document.getElementById('btn-prev-page');
    if (prevBtn) {
        prevBtn.onclick = () => {
            if (currentPage > 1) {
                currentPage--;
                loadTrades();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        };
    }
    
    // Next Page click
    const nextBtn = document.getElementById('btn-next-page');
    if (nextBtn) {
        nextBtn.onclick = () => {
            if (currentPage < totalPages) {
                currentPage++;
                loadTrades();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        };
    }
}

// Dom Loaded
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initHistoryPage, 500);
});
