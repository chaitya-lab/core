"""Shared JavaScript utilities for fake web pages.

This module provides client-side tracking for browser automation tests:
- Console capture (log, error, warn)
- DOM event tracking (clicks, inputs, navigations)
- State management for test verification
"""

# JavaScript code to inject into pages
CONSOLE_CAPTURE_JS = """
<script>
// =============================================================================
// Console Capture - Track console.log/error/warn calls
// =============================================================================
window.__chaitya_test__ = {
    consoleLogs: [],
    events: [],
    clicks: [],
    inputs: [],
    navigations: []
};

// Override console methods
(function() {
    const originalConsole = {
        log: console.log.bind(console),
        error: console.error.bind(console),
        warn: console.warn.bind(console),
        info: console.info.bind(console)
    };

    const capture = (type) => (...args) => {
        window.__chaitya_test__.consoleLogs.push({
            type: type,
            message: args.map(a => {
                if (a === null) return 'null';
                if (a === undefined) return 'undefined';
                if (typeof a === 'object') {
                    try { return JSON.stringify(a); } catch { return String(a); }
                }
                return String(a);
            }).join(' '),
            timestamp: Date.now()
        });
        originalConsole[type](...args);
    };

    console.log = capture('log');
    console.error = capture('error');
    console.warn = capture('warn');
    console.info = capture('info');

    // Capture uncaught errors
    window.onerror = (msg, url, line, col, error) => {
        window.__chaitya_test__.consoleLogs.push({
            type: 'error',
            message: '[Uncaught] ' + String(msg),
            timestamp: Date.now()
        });
    };

    // Capture unhandled promise rejections
    window.onunhandledrejection = (event) => {
        window.__chaitya_test__.consoleLogs.push({
            type: 'error',
            message: '[Unhandled Promise] ' + String(event.reason),
            timestamp: Date.now()
        });
    };
})();

// Log page load
console.log('[ChaityaTest] Page loaded:', window.location.href);
</script>
"""

# DOM event tracking
EVENT_TRACKING_JS = """
<script>
// =============================================================================
// DOM Event Tracking - Track clicks, inputs, form submissions
// =============================================================================
document.addEventListener('DOMContentLoaded', function() {
    // Track clicks
    document.addEventListener('click', function(e) {
        const target = e.target;
        window.__chaitya_test__.clicks.push({
            element: target.id || target.className || target.tagName,
            selector: getSelector(target),
            text: target.textContent?.trim().substring(0, 50) || '',
            x: e.clientX,
            y: e.clientY,
            timestamp: Date.now()
        });
        console.log('[ChaityaTest] Click:', getSelector(target));
    });

    // Track inputs
    document.addEventListener('input', function(e) {
        const target = e.target;
        if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
            window.__chaitya_test__.inputs.push({
                element: target.id || target.name || target.tagName,
                selector: getSelector(target),
                value: target.value.substring(0, 100),
                timestamp: Date.now()
            });
            console.log('[ChaityaTest] Input:', getSelector(target), '=', target.value.substring(0, 30));
        }
    });

    // Track form submissions
    document.addEventListener('submit', function(e) {
        e.preventDefault();
        const form = e.target;
        const formData = new FormData(form);
        const data = {};
        for (let [key, value] of formData.entries()) {
            data[key] = value;
        }
        window.__chaitya_test__.events.push({
            type: 'form_submit',
            form: form.id || form.className || 'form',
            selector: getSelector(form),
            data: data,
            timestamp: Date.now()
        });
        console.log('[ChaityaTest] Form submit:', getSelector(form), data);
        
        // If form has action, submit it
        if (form.action && form.method.toLowerCase() === 'post') {
            fetch(form.action, {
                method: 'POST',
                body: formData
            }).then(r => r.text()).then(html => {
                document.body.innerHTML = html;
                console.log('[ChaityaTest] Form response received');
            });
        }
    });

    // Track tab switches
    document.addEventListener('click', function(e) {
        const target = e.target;
        if (target.hasAttribute('data-tab')) {
            const tabId = target.getAttribute('data-tab');
            const tabPanel = document.getElementById(tabId);
            if (tabPanel) {
                // Hide all panels
                document.querySelectorAll('[role="tabpanel"]').forEach(p => p.style.display = 'none');
                // Show selected panel
                tabPanel.style.display = 'block';
                // Update tab states
                document.querySelectorAll('[role="tab"]').forEach(t => t.setAttribute('aria-selected', 'false'));
                target.setAttribute('aria-selected', 'true');
                
                window.__chaitya_test__.events.push({
                    type: 'tab_switch',
                    tab: tabId,
                    timestamp: Date.now()
                });
                console.log('[ChaityaTest] Tab switch:', tabId);
            }
        }
    });
});

// Helper to generate unique selector
function getSelector(element) {
    if (element.id) return '#' + element.id;
    if (element.className && typeof element.className === 'string') {
        return element.tagName.toLowerCase() + '.' + element.className.split(' ')[0];
    }
    return element.tagName.toLowerCase();
}
</script>
"""

# Shared CSS for consistent styling
SHARED_CSS = """
<style>
* {
    box-sizing: border-box;
}
body { 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
    max-width: 900px; 
    margin: 0 auto; 
    padding: 20px;
    background: #fafafa;
}
h1 { color: #1a1a2e; border-bottom: 2px solid #4361ee; padding-bottom: 10px; }
h2 { color: #16213e; margin-top: 30px; }
nav { 
    background: #fff; 
    padding: 15px; 
    border-radius: 8px; 
    margin: 20px 0;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
nav a { 
    margin-right: 20px; 
    text-decoration: none; 
    color: #4361ee;
    font-weight: 500;
}
nav a:hover { text-decoration: underline; }
nav a.active { color: #1a1a2e; font-weight: 700; }
.card {
    background: #fff;
    border-radius: 12px;
    padding: 25px;
    margin: 20px 0;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}
input, textarea, select { 
    padding: 12px; 
    border: 2px solid #e0e0e0;
    border-radius: 8px;
    font-size: 14px;
    width: 100%;
    margin: 5px 0;
}
input:focus, textarea:focus, select:focus {
    outline: none;
    border-color: #4361ee;
}
button {
    padding: 12px 24px;
    background: #4361ee;
    color: white;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-weight: 600;
    margin: 5px;
}
button:hover { background: #3a56d4; }
button.secondary { background: #6c757d; }
button.danger { background: #dc3545; }
button.success { background: #28a745; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
.result-item {
    padding: 15px;
    border-bottom: 1px solid #eee;
    background: #fff;
}
.result-item:hover { background: #f8f9fa; }
.hidden { display: none !important; }
.success { color: #28a745; font-weight: 600; }
.error { color: #dc3545; font-weight: 600; }
.warning { color: #ffc107; }
.loading {
    text-align: center;
    padding: 20px;
    color: #666;
}
.loading::after {
    content: '...';
    animation: dots 1.5s steps(4, end) infinite;
}
@keyframes dots {
    0%, 20% { content: '.'; }
    40% { content: '..'; }
    60%, 100% { content: '...'; }
}
.status-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
}
.status-badge.active { background: #d4edda; color: #155724; }
.status-badge.inactive { background: #f8d7da; color: #721c24; }
.status-badge.pending { background: #fff3cd; color: #856404; }
</style>
"""


# Navigation HTML
def get_nav_html(current_page: str = "") -> str:
    pages = [
        ("/", "Home"),
        ("/search", "Search"),
        ("/chat", "Chat"),
        ("/dashboard", "Dashboard"),
        ("/tabs", "Tabs"),
        ("/scroll", "Scroll"),
        ("/login", "Login"),
        ("/slow", "Slow"),
        ("/error", "Error"),
    ]
    nav = "<nav>"
    for url, name in pages:
        active = "active" if current_page == url else ""
        nav += f'<a href="{url}" class="{active}">{name}</a>'
    nav += "</nav>"
    return nav


# Standard page header
def get_page_header(title: str, current_page: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} - Chaitya Test</title>
{SHARED_CSS}
</head>
<body>
<h1>{title}</h1>
{get_nav_html(current_page)}
"""


# Standard page footer
def get_page_footer() -> str:
    return """
<div style="text-align:center; color:#666; margin-top:40px; padding:20px; border-top:1px solid #eee;">
    <small>Chaitya Test Web Suite v1.0 | 
    <a href="/api/state">State</a> | 
    <a href="/api/console">Console</a> | 
    <a href="/api/reset">Reset</a>
    </small>
</div>
</body></html>"""
