/**
 * BinBot PRO - Common Application Script
 * Handles JWT Authentication, API requests, Socket.IO connections, layout rendering, and Toast notifications.
 */

// JWT and Session Keys
const TOKEN_KEY = 'binbot_access_token';
const REFRESH_KEY = 'binbot_refresh_token';

// API Fetch Wrapper with Auth and Auto-Refresh
async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    
    // Attach Access Token if exists
    const accessToken = localStorage.getItem(TOKEN_KEY);
    if (accessToken) {
        options.headers['Authorization'] = `Bearer ${accessToken}`;
    }
    
    let response = await fetch(url, options);
    
    // Handle Token Expiration (401 Unauthorized)
    if (response.status === 401) {
        console.warn("Access token expired or unauthorized. Attempting refresh...");
        const refreshed = await attemptTokenRefresh();
        if (refreshed) {
            // Retry request with new token
            const newAccessToken = localStorage.getItem(TOKEN_KEY);
            options.headers['Authorization'] = `Bearer ${newAccessToken}`;
            response = await fetch(url, options);
        } else {
            // Refresh failed, redirect to login
            logout();
            return null;
        }
    }
    
    return response;
}

// Attempt to refresh JWT tokens
async function attemptTokenRefresh() {
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (!refreshToken) return false;
    
    try {
        const res = await fetch(`/api/auth/refresh?refresh_token=${encodeURIComponent(refreshToken)}`, {
            method: 'POST'
        });
        
        if (res.ok) {
            const data = await res.json();
            localStorage.setItem(TOKEN_KEY, data.access_token);
            localStorage.setItem(REFRESH_KEY, data.refresh_token);
            console.log("JWT Token refreshed successfully.");
            return true;
        }
    } catch (e) {
        console.error("Error refreshing token:", e);
    }
    return false;
}

// Auth Guard Check
function checkAuth() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token && !window.location.pathname.endsWith('/login') && !window.location.pathname.endsWith('/login.html')) {
        window.location.href = '/login';
    }
}

// Logout
function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    showToast("Logged out successfully", "info");
    setTimeout(() => {
        window.location.href = '/login';
    }, 1000);
}

// Toast Notification System
function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';
    if (type === 'warning') icon = '⚠️';
    
    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);
    
    // Auto-remove toast after 4s
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s reverse forwards';
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 4000);
}

// Global Socket Instance
let socket = null;

// Initialize Socket.IO connection
function initSocket() {
    if (typeof io === 'undefined') {
        console.warn("Socket.IO client library not loaded. Retrying in 1s...");
        setTimeout(initSocket, 1000);
        return;
    }
    
    const host = window.location.hostname === 'localhost' ? '127.0.0.1' : window.location.hostname;
    socket = io(`http://${host}:${window.location.port}`);
    
    socket.on('connect', () => {
        console.log("Socket.IO connection established.");
        // Re-subscribe if we have active symbols
        const activeSymbol = localStorage.getItem('binbot_active_symbol') || 'BTCUSDT';
        socket.emit('subscribe_prices', { symbols: [activeSymbol] });
    });
    
    socket.on('disconnect', () => {
        console.warn("Socket.IO connection lost.");
    });
    
    socket.on('bot_status', (data) => {
        updateBotStatusBadge(data.status);
    });

    socket.on('risk_alert', (data) => {
        showToast(`[RISK ALERT] ${data.message}`, 'error');
    });

    socket.on('log', (data) => {
        // Find if there is a logs container on the current page
        const lcon = document.getElementById('logsContainer');
        if (lcon) {
            const entry = document.createElement('div');
            const levelClass = data.level === 'error' ? 'error' : (data.level === 'warning' ? 'warning' : (data.level === 'trade' ? 'success' : 'info'));
            const levelColor = data.level === 'error' ? '#ef4444' : (data.level === 'warning' ? '#f59e0b' : (data.level === 'trade' ? '#22c55e' : '#3b82f6'));
            entry.className = `log-line ${levelClass}`;
            entry.innerHTML = `
                <span style="color: #64748b">${new Date().toLocaleTimeString()}</span>
                <b style="color: ${levelColor}">${data.level.toUpperCase()}</b>
                ${data.message}
            `;
            lcon.insertBefore(entry, lcon.firstChild);
            // Cap at 200 logs
            while (lcon.children.length > 200) {
                lcon.removeChild(lcon.lastChild);
            }
        }
    });
}

// Initialize Sidebar and Top Header Layout
function initializeLayout(activePage = '') {
    checkAuth();
    
    // 1. Render Sidebar
    const sidebarContainer = document.getElementById('sidebar-container');
    if (sidebarContainer) {
        sidebarContainer.innerHTML = `
            <aside class="sidebar">
                <div class="sidebar-brand">
                    <span>BinBot</span> PRO <span style="font-size: 0.75rem; background: var(--primary-glow); color: var(--primary); border: 1px solid rgba(59, 130, 246, 0.4); padding: 0.1rem 0.45rem; border-radius: 6px; font-weight: 600; margin-left: 0.25rem; letter-spacing: 0.5px;">v2</span>
                </div>
                <ul class="sidebar-menu">
                    <li>
                        <a href="/dashboard" class="sidebar-link ${activePage === 'overview' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z"></path></svg>
                            Overview
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/ai-mode" class="sidebar-link ${activePage === 'ai-mode' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                            AI Auto Mode
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/positions" class="sidebar-link ${activePage === 'positions' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z"></path></svg>
                            Active Positions
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/history" class="sidebar-link ${activePage === 'history' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                            Trade History
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/scanner" class="sidebar-link ${activePage === 'scanner' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                            Pair Scanner
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/signals" class="sidebar-link ${activePage === 'signals' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
                            Signal Analysis
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/analytics" class="sidebar-link ${activePage === 'analytics' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                            Analytics
                        </a>
                    </li>
                    <li>
                        <a href="/dashboard/settings" class="sidebar-link ${activePage === 'settings' ? 'active' : ''}">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
                            Settings
                        </a>
                    </li>
                </ul>
                <div class="sidebar-footer">
                    <div class="user-info">
                        <span>Admin Session</span>
                        <button onclick="logout()" class="logout-btn">
                            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="width:1rem;height:1rem;"><path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                            Logout
                        </button>
                    </div>
                </div>
            </aside>
        `;
    }
    
    // 2. Render Top Header
    const headerContainer = document.getElementById('header-container');
    if (headerContainer) {
        headerContainer.innerHTML = `
            <header class="top-header">
                <div class="header-status">
                    <div id="bot-status-badge" class="status-badge idle">
                        <span id="bot-status-dot" class="pulse-dot"></span>
                        <span id="bot-status-text">Loading...</span>
                    </div>
                </div>
                <div class="header-metrics">
                    <div class="header-metric-item">
                        <div class="metric-label">Account Balance</div>
                        <div id="hdr-wallet-balance" class="metric-value">$0.00</div>
                    </div>
                    <div class="header-metric-item">
                        <div class="metric-label">Today's PnL</div>
                        <div id="hdr-daily-pnl" class="metric-value">$0.00</div>
                    </div>
                    <div class="header-metric-item">
                        <div class="metric-label">Active Trades</div>
                        <div id="hdr-active-trades" class="metric-value">0 / 3</div>
                    </div>
                </div>
            </header>
        `;
    }
    
    // Initial fetch for status & metrics
    updateHeaderStats();
    setInterval(updateHeaderStats, 10000);
}

// Update Header Metrics and Status Badge
async function updateHeaderStats() {
    try {
        const [statsRes, statusRes] = await Promise.all([
            apiFetch('/api/analytics/overview'),
            apiFetch('/api/bot/status')
        ]);
        
        if (statsRes && statsRes.ok) {
            const stats = await statsRes.json();
            const balanceElem = document.getElementById('hdr-wallet-balance');
            const pnlElem = document.getElementById('hdr-daily-pnl');
            const activeElem = document.getElementById('hdr-active-trades');
            
            if (balanceElem) {
                const balance = typeof stats.balance === 'number' ? stats.balance : 0;
                balanceElem.innerText = `$${balance.toFixed(2)}`;
            }
            if (pnlElem) {
                const todayPnl = typeof stats.today_pnl === 'number' ? stats.today_pnl : 0;
                pnlElem.innerText = `${todayPnl >= 0 ? '+' : ''}$${todayPnl.toFixed(2)}`;
                pnlElem.className = `metric-value ${todayPnl >= 0 ? 'pnl-pos' : 'pnl-neg'}`;
            }
            if (activeElem) activeElem.innerText = `${stats.active_positions} / 3`;
        }
        
        if (statusRes && statusRes.ok) {
            const status = await statusRes.json();
            if (status && status.status) {
                updateBotStatusBadge(status.status.toUpperCase());
            }
        }
    } catch (e) {
        console.error("Error updating header stats:", e);
    }
}

// Update Bot Status Badge classes
function updateBotStatusBadge(status) {
    const badge = document.getElementById('bot-status-badge');
    const dot = document.getElementById('bot-status-dot');
    const txt = document.getElementById('bot-status-text');
    
    if (!badge || !txt) return;
    
    // Reset classes
    badge.className = 'status-badge';
    if (dot) dot.className = 'pulse-dot';
    
    if (status === 'RUNNING') {
        badge.classList.add('running');
        if (dot) dot.classList.add('anim');
        txt.innerText = 'AI Scanning';
    } else if (status === 'PAUSED') {
        badge.classList.add('paused');
        txt.innerText = 'Paused';
    } else if (status === 'ERROR') {
        badge.classList.add('error');
        txt.innerText = 'Error Alert';
    } else {
        badge.classList.add('idle');
        txt.innerText = 'Bot Idle';
    }
}

// On Script Load, init Socket.IO and perform Auth check
document.addEventListener('DOMContentLoaded', () => {
    // If not on login page, startup Socket.IO
    if (!window.location.pathname.endsWith('/login') && !window.location.pathname.endsWith('/login.html')) {
        initSocket();
    }
});
