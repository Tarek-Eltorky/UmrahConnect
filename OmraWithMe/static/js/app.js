// API Helper
const API = {
    baseUrl: '',
    
    getToken() {
        return localStorage.getItem('omra_token');
    },

    setToken(token, refreshToken) {
        localStorage.setItem('omra_token', token);
        if (refreshToken) this.setRefresh(refreshToken);
    },

    getRefresh() {
        return localStorage.getItem('umrah_refresh');
    },

    setRefresh(t) {
        localStorage.setItem('umrah_refresh', t);
    },

    setUser(user) {
        localStorage.setItem('omra_user', JSON.stringify(user));
    },

    getUser() {
        const user = localStorage.getItem('omra_user');
        return user ? JSON.parse(user) : null;
    },

    clearToken() {
        localStorage.removeItem('omra_token');
        localStorage.removeItem('omra_user');
        localStorage.removeItem('umrah_refresh');
    },

    logout() {
        this.clearToken();
        window.location.href = '/';
    },
    
    isLoggedIn() {
        return !!this.getToken();
    },
    
    // Exchange the stored refresh token for a fresh token pair.
    // Returns true when a new access token was stored, false otherwise.
    async _tryRefresh() {
        const refreshToken = this.getRefresh();
        if (!refreshToken) return false;
        try {
            const res = await fetch(`${this.baseUrl}/api/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken })
            });
            if (!res.ok) return false;
            let data = null;
            try { data = await res.json(); } catch (e) { return false; }
            if (data && data.token) {
                this.setToken(data.token);
                if (data.refresh_token) this.setRefresh(data.refresh_token);
                return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    },

    async request(endpoint, options = {}, _isRetry = false) {
        const t = (k) => (window.I18N ? I18N.t(k) : k);
        const token = this.getToken();
        const headers = {
            'Content-Type': 'application/json',
            ...(token && { 'Authorization': `Bearer ${token}` })
        };

        // Network failure (offline, DNS, CORS) → friendly translated message.
        let response;
        try {
            response = await fetch(`${this.baseUrl}${endpoint}`, {
                ...options,
                headers: { ...headers, ...options.headers }
            });
        } catch (e) {
            throw new Error(t('conn_error'));
        }

        // 401: try a silent refresh-token retry once, then fall back to
        // the existing clear-and-redirect behavior.
        // Skip the auto-redirect if the call itself was the login/register attempt.
        if (response.status === 401 && !endpoint.startsWith('/api/login') && !endpoint.startsWith('/api/register')) {
            if (!_isRetry && !endpoint.startsWith('/api/refresh') && this.getRefresh()) {
                const refreshed = await this._tryRefresh();
                if (refreshed) {
                    return this.request(endpoint, options, true);
                }
            }
            const wasLoggedIn = !!this.getToken();
            this.clearToken();
            if (wasLoggedIn && !location.pathname.startsWith('/login')) {
                const next = encodeURIComponent(location.pathname + location.search);
                location.href = `/login?session_expired=1&next=${next}`;
                throw new Error('Session expired');
            }
        }

        // Parse JSON safely: a proxy 502/504 etc. returns HTML — surface a
        // translated connection error instead of a raw SyntaxError.
        let data = null;
        try {
            data = await response.json();
        } catch (e) {
            if (!response.ok) throw new Error(t('conn_error'));
            return null; // ok response with empty/non-JSON body
        }

        // Capture a rotated refresh token from any successful response
        // (login/register/refresh) without needing template changes.
        if (response.ok && data && data.refresh_token) {
            this.setRefresh(data.refresh_token);
        }
        // Templates own storing the access token after login/register;
        // /api/refresh is owned here.
        if (response.ok && data && data.token && endpoint.startsWith('/api/refresh')) {
            this.setToken(data.token);
        }

        if (!response.ok) {
            // FastAPI 422 returns detail as an array of {loc, msg, type} objects.
            // Flatten it to a readable string so users don't see [object Object].
            let msg = data.detail;
            if (Array.isArray(msg)) {
                msg = msg.map(err => {
                    const field = Array.isArray(err.loc) ? err.loc.slice(1).join('.') : '';
                    return field ? `${field}: ${err.msg}` : err.msg;
                }).join(' • ');
            } else if (msg && typeof msg === 'object') {
                msg = msg.msg || JSON.stringify(msg);
            }
            // Map machine error codes to friendly translated messages.
            if (msg === 'verification_required') msg = t('verify_required');
            else if (msg === 'conversation_closed') msg = t('chat_closed');
            throw new Error(msg || t('error_generic'));
        }

        return data;
    },

    // Cached /api/me — fetched at most once per page load.
    _mePromise: null,
    me() {
        if (!this._mePromise) {
            this._mePromise = this.request('/api/me').catch(e => {
                this._mePromise = null; // allow retry after failure
                throw e;
            });
        }
        return this._mePromise;
    },
    
    get(endpoint) {
        return this.request(endpoint);
    },
    
    post(endpoint, data) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },

    patch(endpoint, data) {
        return this.request(endpoint, {
            method: 'PATCH',
            body: JSON.stringify(data)
        });
    },

    put(endpoint, data) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    delete(endpoint) {
        return this.request(endpoint, { method: 'DELETE' });
    }
};

// UI Helpers
const UI = {
    // HTML-escape user-generated content before putting it in innerHTML.
    // EVERY interpolation of server data into a template literal must go through this.
    esc(str) {
        return String(str ?? '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    showAlert(message, type = 'success') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.textContent = message;

        // Announce alerts to screen readers: mark the live region on the
        // dedicated container if present, otherwise on the injected div itself.
        const alertContainer = document.getElementById('alertContainer');
        if (alertContainer) {
            alertContainer.setAttribute('aria-live', 'polite');
            alertContainer.setAttribute('role', 'status');
        } else {
            alertDiv.setAttribute('aria-live', 'polite');
            alertDiv.setAttribute('role', 'status');
        }

        const container = alertContainer || document.querySelector('.container') || document.body;
        container.insertBefore(alertDiv, container.firstChild);

        try {
            const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            alertDiv.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' });
        } catch (e) {}

        setTimeout(() => alertDiv.remove(), 5000);
    },
    
    showLoading(container) {
        container.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <p>Loading...</p>
            </div>
        `;
    },
    
    _intlLocale() {
        return (window.I18N && I18N.get() === 'ar') ? 'ar-EG' : 'en-US';
    },

    formatDate(dateStr) {
        const date = new Date(dateStr);
        return new Intl.DateTimeFormat(this._intlLocale(), {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        }).format(date);
    },

    formatRelative(dateStr) {
        const t = (k) => (window.I18N ? I18N.t(k) : k);
        // Backend sends UTC ISO strings ("...Z"); tolerate legacy values
        // without the trailing Z by appending it only when missing.
        const str = String(dateStr);
        const date = new Date(str.includes('Z') || str.includes('+') ? str : str + 'Z');
        const now = new Date();
        const diffMs = now - date;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return t('rel_just_now');
        // rel_fmt is a template: "{n}{u}" (EN) / "منذ {n}{u}" (AR)
        const fmt = (n, unitKey) => t('rel_fmt').replace('{n}', n).replace('{u}', t(unitKey));
        if (diffMin < 60) return fmt(diffMin, 'rel_m');
        const diffH = Math.floor(diffMin / 60);
        if (diffH < 24) return fmt(diffH, 'rel_h');
        const diffD = Math.floor(diffH / 24);
        return fmt(diffD, 'rel_d');
    },

    formatCurrency(amount) {
        const locale = (window.I18N && I18N.get() === 'ar') ? 'ar-EG' : 'en-EG';
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: 'EGP',
            maximumFractionDigits: 0
        }).format(amount);
    },

    // WhatsApp deep link with Egyptian phone normalization (defense for
    // legacy data — the backend now normalizes to +20...).
    waLink(phone) {
        let digits = String(phone || '').replace(/\D/g, '');
        if (digits.startsWith('00')) {
            digits = digits.replace(/^0+/, '');
        } else if (digits.startsWith('01') && digits.length === 11) {
            digits = '2' + digits;
        }
        return 'https://wa.me/' + digits;
    },
    
    renderStars(count) {
        return '★'.repeat(count) + '☆'.repeat(5 - count);
    },
    
    updateNavbar() {
        const navLinks = document.querySelector('.nav-links');
        if (!navLinks) return;
        const t = (k) => (window.I18N ? I18N.t(k) : k);
        const currentLocale = window.I18N ? I18N.get() : 'en';
        const otherLocale = currentLocale === 'ar' ? 'en' : 'ar';
        const langLabel = otherLocale === 'ar' ? 'العربية' : 'English';

        if (API.isLoggedIn()) {
            const user = API.getUser();
            const initials = (user?.full_name || 'U').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
            const firstName = user?.full_name?.split(' ')[0] || 'Me';
            navLinks.innerHTML = `
                <a href="/">${t('nav_browse')}</a>
                <a href="/dashboard">${t('nav_dashboard')}</a>
                <a href="/create-announcement">${t('nav_create')}</a>
                <a href="/my-requests">${t('nav_requests')}</a>
                <button class="lang-toggle" type="button" onclick="UI.toggleLocale()" title="${t('language')}" aria-label="${t('language')}">
                    <i class="fas fa-globe"></i> <span class="lang-toggle-label">${langLabel}</span>
                </button>
                <div class="notif-bell-wrap">
                    <button class="notif-bell" onclick="Notifications.toggle()" title="${t('notif_title')}" aria-label="${t('notif_title')}" aria-haspopup="true" aria-expanded="false" aria-controls="notifDropdown">
                        🔔
                        <span class="notif-badge"></span>
                    </button>
                    <div class="notif-dropdown" id="notifDropdown">
                        <div class="notif-dropdown-header">
                            <span>${t('notif_title')}</span>
                            <button class="notif-read-all" onclick="Notifications.markAllRead()">${t('notif_mark_all')}</button>
                        </div>
                        <div class="notif-list"><div class="notif-empty">${t('loading')}</div></div>
                    </div>
                </div>
                <div class="profile-dropdown-wrap">
                    <button class="profile-dropdown-btn" onclick="UI.toggleProfileDropdown(event)" aria-haspopup="true" aria-expanded="false" aria-controls="profileDropdown">
                        <span class="nav-avatar">${initials}</span>
                        <span>${firstName}</span>
                        <i class="fas fa-chevron-down profile-caret"></i>
                    </button>
                    <div class="profile-dd" id="profileDropdown">
                        <div class="profile-dd-header">
                            <div class="profile-dd-avatar">${initials}</div>
                            <div>
                                <div class="profile-dd-name">${user?.full_name || ''}</div>
                            </div>
                        </div>
                        <a href="/profile" class="profile-dd-item">
                            <i class="fas fa-user-edit"></i> ${t('nav_profile')}
                        </a>
                        <div class="profile-dd-divider"></div>
                        <a href="#" class="profile-dd-item profile-dd-danger"
                            onclick="API.logout(); return false;">
                            <i class="fas fa-sign-out-alt"></i> ${t('nav_logout')}
                        </a>
                    </div>
                </div>
            `;
            Notifications.startPolling();
        } else {
            navLinks.innerHTML = `
                <a href="/">${t('nav_home')}</a>
                <a href="/login">${t('nav_login')}</a>
                <a href="/register" class="btn btn-primary">${t('nav_register')}</a>
                <button class="lang-toggle" type="button" onclick="UI.toggleLocale()" title="${t('language')}" aria-label="${t('language')}">
                    <i class="fas fa-globe"></i> <span class="lang-toggle-label">${langLabel}</span>
                </button>
            `;
        }
    },

    toggleLocale() {
        if (!window.I18N) return;
        const next = I18N.get() === 'ar' ? 'en' : 'ar';
        I18N.set(next);
        UI.updateNavbar();
        _renderMobileNav();
        // Persist on server for logged-in users
        if (API.isLoggedIn()) {
            API.put('/api/me', { locale: next }).catch(() => {});
        }
    },

    // Keep a dropdown trigger button's aria-expanded in sync with its menu.
    _syncExpanded(btnSelector, open) {
        const btn = document.querySelector(btnSelector);
        if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    },

    toggleProfileDropdown(e) {
        e.stopPropagation();
        // Close notification dropdown first
        document.getElementById('notifDropdown')?.classList.remove('open');
        UI._syncExpanded('.notif-bell', false);
        const dd = document.getElementById('profileDropdown');
        if (dd) {
            dd.classList.toggle('open');
            UI._syncExpanded('.profile-dropdown-btn', dd.classList.contains('open'));
        }
    }
};

// ─── Notifications ────────────────────────────────────────────────────────
const Notifications = {
    _prevCount: 0,
    _pollTimer: null,

    async fetchCount() {
        if (!API.isLoggedIn()) return;
        try {
            const data = await API.get('/api/notifications/unread-count');
            const count = data.count || 0;
            this._updateBadge(count);
            // Shake bell if new notifications arrived
            if (count > this._prevCount && this._prevCount !== null) {
                const bell = document.querySelector('.notif-bell');
                if (bell) {
                    bell.classList.remove('notif-shake');
                    void bell.offsetWidth;
                    bell.classList.add('notif-shake');
                    setTimeout(() => bell.classList.remove('notif-shake'), 700);
                }
            }
            this._prevCount = count;
        } catch (e) {}
    },

    _updateBadge(count) {
        const badge = document.querySelector('.notif-badge');
        if (!badge) return;
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = count > 0 ? 'flex' : 'none';
    },

    _icon(type) {
        const map = { new_request: '🙋', accepted: '✅', rejected: '❌', comment: '💬', new_message: '✉️' };
        return map[type] || '🔔';
    },

    _escape(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    async fetchAndRender() {
        if (!API.isLoggedIn()) return;
        const dropdown = document.getElementById('notifDropdown');
        if (!dropdown) return;
        const list = dropdown.querySelector('.notif-list');
        const t = (k) => (window.I18N ? I18N.t(k) : k);
        try {
            const data = await API.get('/api/notifications');
            if (!data.items || data.items.length === 0) {
                list.innerHTML = `<div class="notif-empty">${t('notif_empty')} 🕊️</div>`;
                return;
            }
            list.innerHTML = data.items.map(n => `
                <a class="notif-item ${n.is_read ? '' : 'unread'}"
                   href="${n.link}"
                   onclick="Notifications.markRead(${n.id})">
                    <div class="notif-icon ${n.notif_type}">${this._icon(n.notif_type)}</div>
                    <div class="notif-text">
                        <div class="notif-msg">${this._escape(n.message)}</div>
                        <div class="notif-time">${UI.formatRelative(n.created_at)}</div>
                    </div>
                </a>
            `).join('');
        } catch (e) {
            list.innerHTML = `<div class="notif-empty">${t('notif_load_failed')}</div>`;
        }
    },

    async markRead(id) {
        try { await API.post(`/api/notifications/${id}/read`, {}); } catch (e) {}
    },

    async markAllRead() {
        try {
            await API.post('/api/notifications/read-all', {});
            this._updateBadge(0);
            this._prevCount = 0;
            await this.fetchAndRender();
        } catch (e) {}
    },

    toggle() {
        const dropdown = document.getElementById('notifDropdown');
        if (!dropdown) return;
        if (dropdown.classList.contains('open')) {
            dropdown.classList.remove('open');
            UI._syncExpanded('.notif-bell', false);
        } else {
            dropdown.classList.add('open');
            UI._syncExpanded('.notif-bell', true);
            this.fetchAndRender();
        }
    },

    startPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._prevCount = null; // don't shake on first load
        this.fetchCount();
        this._prevCount = 0;    // after first fetch, track changes
        this._pollTimer = setInterval(() => this.fetchCount(), 15000);
    }
};

// ─── Email verification banner ────────────────────────────────────────────
async function _renderVerifyBanner() {
    if (!API.isLoggedIn()) return;
    try {
        if (sessionStorage.getItem('umrah_verify_dismissed')) return;
    } catch (e) {}
    let me;
    try {
        me = await API.me();
    } catch (e) { return; }
    if (!me || me.is_verified !== false) return;
    if (document.getElementById('verifyBanner')) return;

    const t = (k) => (window.I18N ? I18N.t(k) : k);
    const banner = document.createElement('div');
    banner.id = 'verifyBanner';
    banner.className = 'verify-banner';
    banner.setAttribute('role', 'status');
    banner.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:0.75rem;flex-wrap:wrap;padding:0.5rem 1rem;background:#fff8e1;color:#7a5c00;font-size:0.9rem;';
    banner.innerHTML = `
        <span>${t('verify_banner')}</span>
        <button type="button" id="verifyResendBtn" class="btn btn-small" style="cursor:pointer;">${t('verify_resend')}</button>
        <button type="button" id="verifyDismissBtn" aria-label="${t('form_cancel')}" style="cursor:pointer;background:none;border:none;font-size:1.1rem;color:inherit;">✕</button>
    `;
    const navbar = document.querySelector('.navbar');
    if (navbar && navbar.parentNode) {
        navbar.parentNode.insertBefore(banner, navbar.nextSibling);
    } else {
        document.body.insertBefore(banner, document.body.firstChild);
    }
    document.getElementById('verifyResendBtn').addEventListener('click', async () => {
        try {
            await API.post('/api/resend-verification', {});
            UI.showAlert(t('verify_sent'), 'success');
        } catch (e) {
            UI.showAlert(e.message, 'error');
        }
    });
    document.getElementById('verifyDismissBtn').addEventListener('click', () => {
        try { sessionStorage.setItem('umrah_verify_dismissed', '1'); } catch (e) {}
        banner.remove();
    });
}

// Initialize navbar on page load
document.addEventListener('DOMContentLoaded', () => {
    UI.updateNavbar();
    _renderMobileNav();

    // If logged in, sync server-side preferred locale (overrides local).
    // API.me() is cached — the verification banner reuses the same fetch.
    if (API.isLoggedIn() && window.I18N) {
        API.me().then(me => {
            if (me.locale && me.locale !== I18N.get()) {
                I18N.set(me.locale);
                UI.updateNavbar();
                _renderMobileNav();
            }
        }).catch(() => {});
    }

    // Global email-verification banner (auth-aware, per-session dismissible)
    _renderVerifyBanner();

    // Re-render dynamic nav when locale changes
    document.addEventListener('localechange', () => {
        UI.updateNavbar();
        _renderMobileNav();
    });

    // Hamburger + notification dropdown outside-click handler
    document.addEventListener('click', (e) => {
        const hamburgerBtn = e.target.closest('.hamburger');
        if (hamburgerBtn) {
            const nav = document.querySelector('.nav-links');
            if (nav) {
                if (!nav.id) nav.id = 'navLinks';
                nav.classList.toggle('open');
                hamburgerBtn.setAttribute('aria-controls', nav.id);
                hamburgerBtn.setAttribute('aria-expanded', nav.classList.contains('open') ? 'true' : 'false');
            }
        } else if (!e.target.closest('.navbar')) {
            const nav = document.querySelector('.nav-links');
            if (nav) nav.classList.remove('open');
            document.querySelector('.hamburger')?.setAttribute('aria-expanded', 'false');
        }
        // Close notification dropdown when clicking outside the bell wrap
        if (!e.target.closest('.notif-bell-wrap')) {
            const dd = document.getElementById('notifDropdown');
            if (dd) dd.classList.remove('open');
            UI._syncExpanded('.notif-bell', false);
        }
        // Close profile dropdown when clicking outside
        if (!e.target.closest('.profile-dropdown-wrap')) {
            const pd = document.getElementById('profileDropdown');
            if (pd) pd.classList.remove('open');
            UI._syncExpanded('.profile-dropdown-btn', false);
        }
    });

    // Escape closes open dropdowns and the mobile nav
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const dd = document.getElementById('notifDropdown');
        if (dd && dd.classList.contains('open')) {
            dd.classList.remove('open');
            UI._syncExpanded('.notif-bell', false);
        }
        const pd = document.getElementById('profileDropdown');
        if (pd && pd.classList.contains('open')) {
            pd.classList.remove('open');
            UI._syncExpanded('.profile-dropdown-btn', false);
        }
        const nav = document.querySelector('.nav-links');
        if (nav && nav.classList.contains('open')) {
            nav.classList.remove('open');
            document.querySelector('.hamburger')?.setAttribute('aria-expanded', 'false');
        }
    });
});

// bfcache guard: pages restored from the back/forward cache may show
// authed content after logout — re-check auth on protected pages.
window.addEventListener('pageshow', (event) => {
    const guarded = ['/dashboard', '/my-requests', '/profile', '/create-announcement'];
    if (event.persisted && guarded.includes(location.pathname) && !API.isLoggedIn()) {
        location.replace('/login');
    }
});

// Modal Functions (accessible: dialog semantics, focus trap, scroll lock)
const _modalState = { prevFocus: null, keyHandler: null };

function _modalFocusables(modal) {
    return Array.from(modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(el => !el.disabled && el.offsetParent !== null);
}

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.add('active');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');

    _modalState.prevFocus = document.activeElement;

    // Focus the first focusable element inside the modal
    const els = _modalFocusables(modal);
    if (els.length) els[0].focus();

    // Escape closes; Tab wraps focus within the modal (simple focus trap)
    if (_modalState.keyHandler) document.removeEventListener('keydown', _modalState.keyHandler);
    _modalState.keyHandler = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            _closeModalEl(modal, modalId);
            return;
        }
        if (e.key === 'Tab') {
            const focusables = _modalFocusables(modal);
            if (!focusables.length) return;
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (e.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
                e.preventDefault();
                first.focus();
            }
        }
    };
    document.addEventListener('keydown', _modalState.keyHandler);

    // Lock body scroll while the modal is open
    document.body.style.overflow = 'hidden';
}

// Shared close path so cleanup always runs (Escape, overlay click, buttons)
function _closeModalEl(modal, modalId) {
    if (modal) modal.classList.remove('active');
    if (_modalState.keyHandler) {
        document.removeEventListener('keydown', _modalState.keyHandler);
        _modalState.keyHandler = null;
    }
    document.body.style.overflow = '';
    const prev = _modalState.prevFocus;
    _modalState.prevFocus = null;
    if (prev && typeof prev.focus === 'function') {
        try { prev.focus(); } catch (e) {}
    }
    // Notify page-level listeners (e.g. dashboard/my-requests stopChatPolling)
    if (typeof window.__onModalClose === 'function') {
        try { window.__onModalClose(modalId); } catch (e) {}
    }
}

function closeModal(modalId) {
    _closeModalEl(document.getElementById(modalId), modalId);
}

// Close modal on overlay click — routed through the same cleanup path
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('active')) {
        _closeModalEl(e.target, e.target.id || null);
    }
});

// ─── Favorites (saved trips) ────────────────────────────────────────────
const Favorites = {
    _ids: null,

    async load() {
        if (!API.isLoggedIn()) { this._ids = new Set(); return this._ids; }
        if (this._ids) return this._ids;
        try {
            const data = await API.get('/api/me/favorites');
            this._ids = new Set(data.ids || []);
        } catch (e) {
            this._ids = new Set();
        }
        return this._ids;
    },

    has(id) {
        return !!(this._ids && this._ids.has(id));
    },

    async toggle(id) {
        const t = (k) => (window.I18N ? I18N.t(k) : k);
        if (!API.isLoggedIn()) {
            UI.showAlert(t('fav_login_required'), 'error');
            return null;
        }
        await this.load();
        try {
            if (this.has(id)) {
                await API.delete(`/api/announcements/${id}/favorite`);
                this._ids.delete(id);
                UI.showAlert(t('fav_removed'));
                return false;
            } else {
                await API.post(`/api/announcements/${id}/favorite`, {});
                this._ids.add(id);
                UI.showAlert(t('fav_added'));
                return true;
            }
        } catch (e) {
            UI.showAlert(e.message, 'error');
            return null;
        }
    }
};

// ─── Shared footer (terms / privacy / safety) ───────────────────────────
function _renderFooter() {
    const t = (k) => (window.I18N ? I18N.t(k) : k);
    let footer = document.getElementById('siteFooter');
    if (!footer) {
        footer = document.createElement('footer');
        footer.id = 'siteFooter';
        footer.className = 'site-footer';
        const mobileNav = document.getElementById('mobileNavBar');
        document.body.insertBefore(footer, mobileNav || null);
    }
    footer.innerHTML = `
        <div class="site-footer-links">
            <a href="/safety"><i class="fas fa-shield-alt"></i> ${t('footer_safety')}</a>
            <a href="/terms">${t('footer_terms')}</a>
            <a href="/privacy">${t('footer_privacy')}</a>
        </div>
        <div class="site-footer-note">🕋 ${t('app_name')} — ${t('footer_rights')}</div>
    `;
}
document.addEventListener('DOMContentLoaded', () => {
    _renderFooter();
    document.addEventListener('localechange', _renderFooter);
});

// ─── Mobile bottom navigation bar ────────────────────────────────────────
function _renderMobileNav() {
    const bar = document.getElementById('mobileNavBar');
    if (!bar) return;
    const path = window.location.pathname;
    const loggedIn = API.isLoggedIn();
    const t = (k) => (window.I18N ? I18N.t(k) : k);

    const links = [
        { href: '/', icon: 'fa-home', label: t('nav_home') },
        ...(loggedIn ? [
            { href: '/dashboard', icon: 'fa-tachometer-alt', label: t('nav_dashboard') },
            { href: '/my-requests', icon: 'fa-paper-plane', label: t('nav_requests') },
            { href: '/create-announcement', icon: 'fa-plus-circle', label: t('nav_create').replace('+ ', '') },
            { href: '/profile', icon: 'fa-user-circle', label: t('nav_profile') },
        ] : [
            { href: '/login', icon: 'fa-sign-in-alt', label: t('nav_login') },
            { href: '/register', icon: 'fa-user-plus', label: t('nav_register') },
        ])
    ];

    bar.innerHTML = links.map(l => `
        <a href="${l.href}" class="${path === l.href ? 'active' : ''}">
            <i class="fas ${l.icon}"></i>
            <span>${l.label}</span>
        </a>`).join('');
}

// ─── PWA Service Worker Registration + new-version prompt ───────────────
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js').then(reg => {
            // Listen for waiting SW → show "new version" banner
            const showBanner = () => {
                if (document.getElementById('swUpdateBanner')) return;
                const banner = document.createElement('div');
                banner.id = 'swUpdateBanner';
                banner.className = 'sw-update-banner';
                const msg = window.I18N ? I18N.t('new_version_available') : 'A new version is available';
                const action = window.I18N ? I18N.t('refresh') : 'Refresh';
                banner.innerHTML = `<span>${msg}</span><button id="swRefreshBtn">${action}</button>`;
                document.body.appendChild(banner);
                document.getElementById('swRefreshBtn').addEventListener('click', () => {
                    if (reg.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });
                });
            };
            if (reg.waiting) showBanner();
            reg.addEventListener('updatefound', () => {
                const newWorker = reg.installing;
                if (!newWorker) return;
                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        showBanner();
                    }
                });
            });
            let refreshing = false;
            navigator.serviceWorker.addEventListener('controllerchange', () => {
                if (refreshing) return;
                refreshing = true;
                window.location.reload();
            });
        }).catch(() => {});
    });
}

// ─── Web Share (mobile share sheet) ────────────────────────────
UI.share = async function(title, text, url) {
    if (navigator.share) {
        try { await navigator.share({ title, text, url }); return true; } catch (e) { return false; }
    }
    try {
        await navigator.clipboard.writeText(url || window.location.href);
        UI.showAlert(window.I18N ? I18N.t('link_copied') : 'Link copied to clipboard');
        return true;
    } catch (e) { return false; }
};

// ─── Back-to-top button ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.createElement('button');
    btn.id = 'backToTop';
    btn.innerHTML = '<i class="fas fa-chevron-up"></i>';
    btn.setAttribute('aria-label', 'Back to top');
    document.body.appendChild(btn);

    window.addEventListener('scroll', () => {
        btn.classList.toggle('visible', window.scrollY > 300);
    }, { passive: true });

    btn.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
});
