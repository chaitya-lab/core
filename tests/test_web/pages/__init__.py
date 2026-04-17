"""Page generators for fake web test suite.

Each function returns HTML for a specific test page.
These pages simulate common web interactions for browser automation testing.
"""

from tests.test_web.shared import (
    CONSOLE_CAPTURE_JS,
    EVENT_TRACKING_JS,
    get_page_footer,
    get_page_header,
)


def home_page() -> str:
    """Home page with links to all test pages."""
    return f"""{get_page_header("Chaitya Test Web Suite")}
<div class="card">
    <h2>Welcome to the Test Web Suite</h2>
    <p>This server provides deterministic fake web pages for testing browser automation.</p>
    <p>Use the navigation above or click the links below:</p>
    
    <table style="width:100%; margin-top:20px;">
        <tr><th>Page</th><th>Features</th><th>Test Cases</th></tr>
        <tr><td><a href="/search">Search</a></td><td>Search input, results display</td><td>Type, click, verify results</td></tr>
        <tr><td><a href="/chat">Chat</a></td><td>Message input, message list</td><td>Send messages, verify appear</td></tr>
        <tr><td><a href="/dashboard">Dashboard</a></td><td>Widgets, buttons, counters</td><td>Click, evaluate, state changes</td></tr>
        <tr><td><a href="/tabs">Tabs</a></td><td>Tab navigation, panels</td><td>Click tabs, verify content</td></tr>
        <tr><td><a href="/scroll">Scroll</a></td><td>Scrollable content, lazy loading</td><td>Scroll, verify content loads</td></tr>
        <tr><td><a href="/login">Login</a></td><td>Form, validation, auth</td><td>Fill, submit, success/error</td></tr>
        <tr><td><a href="/slow">Slow</a></td><td>Delayed responses</td><td>Test timeouts, loading states</td></tr>
        <tr><td><a href="/error">Error</a></td><td>404/500 simulation</td><td>Test error handling</td></tr>
    </table>
</div>
<div class="card">
    <h3>API Endpoints</h3>
    <ul>
        <li><code>GET /api/console</code> - Get captured console logs</li>
        <li><code>GET /api/state</code> - Get interaction state</li>
        <li><code>POST /api/reset</code> - Reset all state</li>
        <li><code>GET /api/state/clear-console</code> - Clear console logs only</li>
    </ul>
</div>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def search_page(query: str = "") -> str:
    """Search page with query results."""
    # Sample data
    all_items = [
        {"id": 1, "name": "Python Programming", "category": "Books", "price": "$29.99"},
        {"id": 2, "name": "JavaScript Essentials", "category": "Books", "price": "$24.99"},
        {"id": 3, "name": "Web Development", "category": "Courses", "price": "$49.99"},
        {"id": 4, "name": "Data Science with Python", "category": "Courses", "price": "$79.99"},
        {"id": 5, "name": "Machine Learning", "category": "Courses", "price": "$99.99"},
        {"id": 6, "name": "Docker Handbook", "category": "Books", "price": "$19.99"},
        {"id": 7, "name": "Kubernetes Guide", "category": "Books", "price": "$34.99"},
        {"id": 8, "name": "React Fundamentals", "category": "Courses", "price": "$39.99"},
    ]

    # Filter based on query
    results = []
    if query:
        q = query.lower()
        results = [
            item for item in all_items if q in item["name"].lower() or q in item["category"].lower()
        ]
        console_msg = f'[Search] Query: "{query}", Found: {len(results)} results'
    else:
        results = all_items[:5]  # Show first 5 by default
        console_msg = "[Search] Page loaded, showing default results"

    results_html = ""
    for item in results:
        results_html += f"""
        <div class="result-item" id="result-{item["id"]}" data-category="{item["category"]}">
            <div style="display:flex; justify-content:space-between;">
                <div>
                    <strong class="result-name" id="name-{item["id"]}">{item["name"]}</strong>
                    <span class="result-category">({item["category"]})</span>
                </div>
                <span class="result-price">{item["price"]}</span>
            </div>
            <div>
                <button class="view-btn" id="view-{item["id"]}" onclick="viewItem({item["id"]})">View</button>
                <button class="add-btn" id="add-{item["id"]}" onclick="addToCart({item["id"]})">Add to Cart</button>
            </div>
        </div>"""

    if not results:
        results_html = (
            '<div class="result-item"><p class="warning">No results found for your query.</p></div>'
        )

    return f"""{get_page_header("Search", "/search")}
<div class="card">
    <h2>Search Page</h2>
    <form id="search-form" onsubmit="return false;">
        <div style="display:flex; gap:10px;">
            <input type="search" id="search-input" name="q" value="{query}" placeholder="Search for products...">
            <button type="button" id="search-btn" onclick="doSearch()">Search</button>
        </div>
    </form>
    <div id="search-results">
        <h3>Results <span id="result-count">({len(results)})</span></h3>
        {results_html}
    </div>
</div>
<script>
function doSearch() {{
    const q = document.getElementById('search-input').value;
    console.log('[Search] Searching for:', q);
    window.location.href = '/search?q=' + encodeURIComponent(q);
}}
function viewItem(id) {{
    console.log('[Search] Viewing item:', id);
    alert('Viewing item ' + id);
}}
function addToCart(id) {{
    console.log('[Search] Adding to cart:', id);
    const btn = document.getElementById('add-' + id);
    btn.textContent = 'Added!';
    btn.classList.add('success');
    btn.disabled = true;
}}
// Focus search input
document.getElementById('search-input').focus();
console.log('{console_msg}');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def chat_page() -> str:
    """Chat page for messaging tests."""
    messages = [
        {
            "id": 1,
            "user": "System",
            "text": "Welcome to the chat! Send a message.",
            "time": "10:00",
            "type": "system",
        },
        {"id": 2, "user": "Alice", "text": "Hello everyone!", "time": "10:01", "type": "user"},
        {"id": 3, "user": "Bob", "text": "Hi Alice!", "time": "10:02", "type": "user"},
    ]

    messages_html = ""
    for msg in messages:
        msg_class = "system-msg" if msg["type"] == "system" else "user-msg"
        messages_html += f"""
        <div class="message {msg_class}" id="msg-{msg["id"]}">
            <span class="msg-user"><strong>{msg["user"]}</strong></span>
            <span class="msg-time">{msg["time"]}</span>
            <div class="msg-text" id="msg-text-{msg["id"]}">{msg["text"]}</div>
        </div>"""

    return f"""{get_page_header("Chat", "/chat")}
<div class="card">
    <h2>Chat Room</h2>
    <div id="chat-messages" style="height:300px; overflow-y:auto; border:1px solid #ddd; padding:10px; background:#f9f9f9; border-radius:8px;">
        {messages_html}
    </div>
    <form id="chat-form">
        <div style="display:flex; gap:10px; margin-top:10px;">
            <input type="text" id="chat-input" placeholder="Type a message..." autocomplete="off">
            <button type="button" id="send-btn" onclick="sendMessage()">Send</button>
        </div>
    </form>
    <p><small>Messages are tracked. Click send to add a new message.</small></p>
</div>
<script>
let messageId = 4;
function sendMessage() {{
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    
    const container = document.getElementById('chat-messages');
    const msgTime = new Date().toLocaleTimeString([], {{hour: '2-digit', minute:'2-digit'}});
    
    const msgHtml = '<div class="message user-msg" id="msg-' + messageId + '"><span class="msg-user"><strong>You</strong></span><span class="msg-time">' + msgTime + '</span><div class="msg-text" id="msg-text-' + messageId + '">' + text + '</div></div>';
    container.insertAdjacentHTML('beforeend', msgHtml);
    container.scrollTop = container.scrollHeight;
    
    console.log('[Chat] Sent message:', text);
    input.value = '';
    messageId++;
    
    // Simulate response after delay
    setTimeout(() => {{
        const responses = [
            "Got it!",
            "Interesting...",
            "Tell me more.",
            "Thanks for sharing!",
            "That's helpful."
        ];
        const respText = responses[Math.floor(Math.random() * responses.length)];
        const respTime = new Date().toLocaleTimeString([], {{hour: '2-digit', minute:'2-digit'}});
        const respHtml = '<div class="message user-msg" style="background:#e3f2fd;"><span class="msg-user"><strong>Bot</strong></span><span class="msg-time">' + respTime + '</span><div class="msg-text">' + respText + '</div></div>';
        container.insertAdjacentHTML('beforeend', respHtml);
        container.scrollTop = container.scrollHeight;
        console.log('[Chat] Bot response:', respText);
    }}, 1000);
}}
// Enter key to send
document.getElementById('chat-input').addEventListener('keypress', function(e) {{
    if (e.key === 'Enter' && !e.shiftKey) {{
        e.preventDefault();
        sendMessage();
    }}
}});
console.log('[Chat] Page loaded');
</script>
<style>
.message {{ padding: 10px; margin: 5px 0; border-radius: 8px; }}
.system-msg {{ background: #fff3cd; }}
.user-msg {{ background: #d4edda; text-align: right; }}
.msg-user {{ font-size: 12px; color: #666; }}
.msg-time {{ font-size: 11px; color: #999; margin-left: 10px; }}
.msg-text {{ margin-top: 5px; }}
</style>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def dashboard_page() -> str:
    """Dashboard with widgets, buttons, and counters."""
    return f"""{get_page_header("Dashboard", "/dashboard")}
<div class="card">
    <h2>Dashboard</h2>
    <p>Interactive dashboard with various widgets for testing.</p>
</div>

<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap:20px;">
    <!-- Counter Widget -->
    <div class="card">
        <h3>Counter</h3>
        <div id="counter-display" style="font-size:48px; text-align:center; font-weight:bold; color:#4361ee;">0</div>
        <div style="text-align:center; margin-top:15px;">
            <button id="counter-inc" onclick="incrementCounter()">+</button>
            <button id="counter-dec" onclick="decrementCounter()">-</button>
            <button id="counter-reset" class="secondary" onclick="resetCounter()">Reset</button>
        </div>
        <p id="counter-status" class="success"></p>
    </div>

    <!-- Toggle Widget -->
    <div class="card">
        <h3>Toggle</h3>
        <label class="switch">
            <input type="checkbox" id="toggle-switch" onchange="toggleSwitch(this)">
            <span class="slider"></span>
        </label>
        <p id="toggle-status">Switch is OFF</p>
        <button onclick="getToggleState()">Get State</button>
        <button class="secondary" onclick="getWidgetValues()">Get All Values</button>
    </div>

    <!-- Select Widget -->
    <div class="card">
        <h3>Dropdown</h3>
        <select id="theme-select" onchange="themeChanged(this)">
            <option value="">Select theme...</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="blue">Blue</option>
            <option value="green">Green</option>
        </select>
        <p id="theme-status"></p>
        <button onclick="getTheme()">Get Selected</button>
    </div>

    <!-- Checkbox Group -->
    <div class="card">
        <h3>Notifications</h3>
        <label><input type="checkbox" id="notif-email" onchange="notifChanged()"> Email</label><br>
        <label><input type="checkbox" id="notif-sms" onchange="notifChanged()"> SMS</label><br>
        <label><input type="checkbox" id="notif-push" onchange="notifChanged()"> Push</label>
        <p id="notif-status"></p>
        <button onclick="getNotifications()">Get Selected</button>
    </div>
</div>

<div class="card">
    <h3>Widget Status</h3>
    <pre id="widget-status" style="background:#f5f5f5; padding:15px; border-radius:8px;"></pre>
</div>

<script>
let counter = 0;

function incrementCounter() {{
    counter++;
    document.getElementById('counter-display').textContent = counter;
    document.getElementById('counter-status').textContent = 'Incremented to ' + counter;
    console.log('[Dashboard] Counter incremented:', counter);
}}

function decrementCounter() {{
    counter--;
    document.getElementById('counter-display').textContent = counter;
    document.getElementById('counter-status').textContent = 'Decremented to ' + counter;
    console.log('[Dashboard] Counter decremented:', counter);
}}

function resetCounter() {{
    counter = 0;
    document.getElementById('counter-display').textContent = counter;
    document.getElementById('counter-status').textContent = 'Reset!';
    console.log('[Dashboard] Counter reset');
}}

function toggleSwitch(el) {{
    const status = el.checked ? 'ON' : 'OFF';
    document.getElementById('toggle-status').textContent = 'Switch is ' + status;
    console.log('[Dashboard] Toggle:', status);
}}

function getToggleState() {{
    const checked = document.getElementById('toggle-switch').checked;
    console.log('[Dashboard] Toggle state:', checked);
    alert('Toggle is ' + (checked ? 'ON' : 'OFF'));
}}

function themeChanged(el) {{
    const value = el.value;
    document.getElementById('theme-status').textContent = value ? 'Selected: ' + value : '';
    console.log('[Dashboard] Theme changed:', value);
}}

function getToggleState() {{
    const checked = document.getElementById('toggle-switch').checked;
    console.log('[Dashboard] Toggle state:', checked);
    alert('Toggle is ' + (checked ? 'ON' : 'OFF'));
}}

function themeChanged(el) {{
    const value = el.value;
    document.getElementById('theme-status').textContent = value ? 'Selected: ' + value : '';
    console.log('[Dashboard] Theme changed:', value);
}}

function getTheme() {{
    const value = document.getElementById('theme-select').value;
    console.log('[Dashboard] Theme:', value);
    alert('Selected theme: ' + (value || 'none'));
}}

function notifChanged() {{
    const email = document.getElementById('notif-email').checked;
    const sms = document.getElementById('notif-sms').checked;
    const push = document.getElementById('notif-push').checked;
    const status = 'Email: ' + email + ', SMS: ' + sms + ', Push: ' + push;
    document.getElementById('notif-status').textContent = status;
    console.log('[Dashboard] Notifications:', status);
}}

function getNotifications() {{
    const email = document.getElementById('notif-email').checked;
    const sms = document.getElementById('notif-sms').checked;
    const push = document.getElementById('notif-push').checked;
    console.log('[Dashboard] Notifications:', {{email, sms, push}});
    alert('Enabled: Email=' + email + ', SMS=' + sms + ', Push=' + push);
}}

function getWidgetValues() {{
    const values = {{
        counter: counter,
        toggle: document.getElementById('toggle-switch').checked,
        theme: document.getElementById('theme-select').value,
        notifications: {{
            email: document.getElementById('notif-email').checked,
            sms: document.getElementById('notif-sms').checked,
            push: document.getElementById('notif-push').checked
        }}
    }};
    document.getElementById('widget-status').textContent = JSON.stringify(values, null, 2);
    console.log('[Dashboard] All widget values:', values);
}}

// Initialize
console.log('[Dashboard] Page loaded');
</script>

<style>
.switch {{
    position: relative; display: inline-block; width: 60px; height: 34px;
}}
.switch input {{ opacity: 0; width: 0; height: 0; }}
.slider {{
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #ccc; transition: .4s; border-radius: 34px;
}}
.slider:before {{
    position: absolute; content: ""; height: 26px; width: 26px; left: 4px; bottom: 4px;
    background-color: white; transition: .4s; border-radius: 50%;
}}
input:checked + .slider {{ background-color: #4361ee; }}
input:checked + .slider:before {{ transform: translateX(26px); }}
</style>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def tabs_page() -> str:
    """Tabs page for testing tab navigation."""
    return f"""{get_page_header("Tabs", "/tabs")}
<div class="card">
    <h2>Tab Navigation</h2>
    <p>Click tabs to switch between content panels.</p>
</div>

<div class="card">
    <div role="tablist" style="display:flex; border-bottom:2px solid #ddd;">
        <button role="tab" id="tab-overview" data-tab="panel-overview" aria-selected="true"
                style="background:none; color:#4361ee; border:none; padding:15px 25px; font-weight:600; cursor:pointer; border-bottom:3px solid #4361ee; margin-bottom:-2px;">
            Overview
        </button>
        <button role="tab" id="tab-analytics" data-tab="panel-analytics" aria-selected="false"
                style="background:none; color:#666; border:none; padding:15px 25px; font-weight:600; cursor:pointer;">
            Analytics
        </button>
        <button role="tab" id="tab-reports" data-tab="panel-reports" aria-selected="false"
                style="background:none; color:#666; border:none; padding:15px 25px; font-weight:600; cursor:pointer;">
            Reports
        </button>
        <button role="tab" id="tab-settings" data-tab="panel-settings" aria-selected="false"
                style="background:none; color:#666; border:none; padding:15px 25px; font-weight:600; cursor:pointer;">
            Settings
        </button>
    </div>

    <div role="tabpanel" id="panel-overview" style="padding:20px;">
        <h3>Overview</h3>
        <p>Welcome to the Overview tab! This is the default selected tab.</p>
        <div id="overview-content">
            <p><strong>Key Metrics:</strong></p>
            <ul>
                <li>Total Users: <span id="metric-users">1,234</span></li>
                <li>Active Sessions: <span id="metric-sessions">56</span></li>
                <li>Revenue: <span id="metric-revenue">$12,345</span></li>
            </ul>
        </div>
        <button onclick="refreshMetrics()">Refresh Metrics</button>
    </div>

    <div role="tabpanel" id="panel-analytics" style="display:none; padding:20px;">
        <h3>Analytics</h3>
        <p>Analytics data would appear here.</p>
        <div id="analytics-chart" style="height:200px; background:#f5f5f5; display:flex; align-items:center; justify-content:center; border-radius:8px;">
            [Chart Placeholder]
        </div>
        <p><button onclick="loadAnalytics()">Load Analytics Data</button></p>
    </div>

    <div role="tabpanel" id="panel-reports" style="display:none; padding:20px;">
        <h3>Reports</h3>
        <p>Generate and view reports here.</p>
        <table style="width:100%;">
            <tr><th>Report</th><th>Date</th><th>Status</th></tr>
            <tr><td>Monthly Sales</td><td>2024-01</td><td><span class="status-badge active">Ready</span></td></tr>
            <tr><td>User Activity</td><td>2024-01</td><td><span class="status-badge pending">Pending</span></td></tr>
            <tr><td>Financial Summary</td><td>2024-01</td><td><span class="status-badge inactive">Draft</span></td></tr>
        </table>
    </div>

    <div role="tabpanel" id="panel-settings" style="display:none; padding:20px;">
        <h3>Settings</h3>
        <form>
            <label>Site Name: <input type="text" id="setting-site-name" value="My Site"></label>
            <label>Language: 
                <select id="setting-language">
                    <option>English</option>
                    <option>Spanish</option>
                    <option>French</option>
                </select>
            </label>
            <button type="button" onclick="saveSettings()">Save Settings</button>
        </form>
    </div>
</div>

<script>
function refreshMetrics() {{
    const users = Math.floor(Math.random() * 10000);
    const sessions = Math.floor(Math.random() * 100);
    const revenue = '$' + (Math.random() * 100000).toFixed(2).replace(/\B(?=(\d{{3}})+( ?))/g, ',');
    
    document.getElementById('metric-users').textContent = users.toLocaleString();
    document.getElementById('metric-sessions').textContent = sessions;
    document.getElementById('metric-revenue').textContent = revenue;
    
    console.log('[Tabs] Metrics refreshed:', {{users, sessions, revenue}});
}}

function loadAnalytics() {{
    const chart = document.getElementById('analytics-chart');
    chart.innerHTML = '<div style="color:#28a745; font-weight:bold;">Analytics Data Loaded!</div><p>Data from Jan 1 - Jan 31</p>';
    console.log('[Tabs] Analytics loaded');
}}

function saveSettings() {{
    const siteName = document.getElementById('setting-site-name').value;
    const language = document.getElementById('setting-language').value;
    console.log('[Tabs] Settings saved:', {{siteName, language}});
    alert('Settings saved!');
}}

console.log('[Tabs] Page loaded');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def scroll_page() -> str:
    """Scroll page with infinite scroll simulation."""
    return f"""{get_page_header("Scroll", "/scroll")}
<div class="card">
    <h2>Infinite Scroll</h2>
    <p>Scroll down to load more content. Loading is tracked.</p>
    <p>Items loaded: <span id="item-count">10</span></p>
</div>

<div class="card" id="content-container">
    <!-- Initial items -->
    <div class="result-item">Item 1</div>
    <div class="result-item">Item 2</div>
    <div class="result-item">Item 3</div>
    <div class="result-item">Item 4</div>
    <div class="result-item">Item 5</div>
    <div class="result-item">Item 6</div>
    <div class="result-item">Item 7</div>
    <div class="result-item">Item 8</div>
    <div class="result-item">Item 9</div>
    <div class="result-item">Item 10</div>
</div>

<div id="loading-indicator" class="loading hidden">Loading more items</div>
<div id="scroll-status" style="text-align:center; padding:20px; color:#666;"></div>

<script>
let itemCount = 10;
let isLoading = false;
let reachedEnd = false;

function loadMoreItems() {{
    if (isLoading || reachedEnd) return;
    
    isLoading = true;
    const indicator = document.getElementById('loading-indicator');
    const container = document.getElementById('content-container');
    const status = document.getElementById('scroll-status');
    
    indicator.classList.remove('hidden');
    console.log('[Scroll] Loading more items...');
    
    // Simulate network delay
    setTimeout(() => {{
        for (let i = 1; i <= 5; i++) {{
            itemCount++;
            const item = document.createElement('div');
            item.className = 'result-item';
            item.textContent = 'Item ' + itemCount;
            item.id = 'scroll-item-' + itemCount;
            container.appendChild(item);
        }}
        
        document.getElementById('item-count').textContent = itemCount;
        indicator.classList.add('hidden');
        isLoading = false;
        
        console.log('[Scroll] Loaded items, total:', itemCount);
        
        if (itemCount >= 50) {{
            reachedEnd = true;
            status.textContent = 'End of content reached!';
            console.log('[Scroll] Reached end of content');
        }}
    }}, 800);
}}

// Intersection Observer for infinite scroll
const observer = new IntersectionObserver((entries) => {{
    entries.forEach(entry => {{
        if (entry.isIntersecting && !isLoading) {{
            loadMoreItems();
        }}
    }});
}}, {{ threshold: 0.1 }});

observer.observe(document.getElementById('scroll-status'));

// Track scroll position
window.addEventListener('scroll', () => {{
    const scrollY = window.scrollY;
    const docHeight = document.documentElement.scrollHeight;
    const winHeight = window.innerHeight;
    const scrollPercent = Math.round((scrollY / (docHeight - winHeight)) * 100);
    
    if (scrollPercent > 0) {{
        console.log('[Scroll] Position:', scrollPercent + '%');
    }}
}});

console.log('[Scroll] Page loaded, items:', itemCount);
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def login_page() -> str:
    """Login page with form validation."""
    return f"""{get_page_header("Login", "/login")}
<div class="card">
    <h2>Login</h2>
    <form id="login-form" action="/api/login" method="post">
        <div>
            <label for="username">Username <span style="color:red">*</span></label>
            <input type="text" id="username" name="username" required autocomplete="username" placeholder="Enter username">
        </div>
        <div>
            <label for="password">Password <span style="color:red">*</span></label>
            <input type="password" id="password" name="password" required autocomplete="current-password" placeholder="Enter password">
        </div>
        <div>
            <label><input type="checkbox" id="remember" name="remember"> Remember me</label>
        </div>
        <div style="margin-top:20px;">
            <button type="submit" id="login-btn">Sign In</button>
            <button type="button" class="secondary" onclick="clearForm()">Clear</button>
        </div>
    </form>
    <div id="login-feedback"></div>
    <p style="margin-top:20px;"><small>Test credentials: admin/secret123 or user/password</small></p>
</div>

<div class="card">
    <h3>Form State</h3>
    <button onclick="getFormValues()">Get Form Values</button>
    <button class="secondary" onclick="validateForm()">Validate Form</button>
    <pre id="form-state" style="background:#f5f5f5; padding:15px; border-radius:8px; margin-top:10px;"></pre>
</div>

<script>
function clearForm() {{
    document.getElementById('login-form').reset();
    document.getElementById('login-feedback').innerHTML = '';
    console.log('[Login] Form cleared');
}}

function getFormValues() {{
    const values = {{
        username: document.getElementById('username').value,
        password: document.getElementById('password').value ? '***' : '',
        remember: document.getElementById('remember').checked
    }};
    document.getElementById('form-state').textContent = JSON.stringify(values, null, 2);
    console.log('[Login] Form values:', values);
}}

function validateForm() {{
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    
    if (!username) {{
        console.error('[Login] Validation error: Username required');
        alert('Username is required');
        return;
    }}
    if (!password) {{
        console.error('[Login] Validation error: Password required');
        alert('Password is required');
        return;
    }}
    
    console.log('[Login] Form valid');
    alert('Form is valid!');
}}

// Handle form submission via fetch
document.getElementById('login-form').addEventListener('submit', async function(e) {{
    e.preventDefault();
    
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    
    console.log('[Login] Submitting:', username);
    
    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.textContent = 'Signing in...';
    
    try {{
        const response = await fetch('/api/login', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ username, password }})
        }});
        
        const data = await response.json();
        
        if (data.success) {{
            console.log('[Login] Success:', data.message);
            document.getElementById('login-feedback').innerHTML = 
                '<p class="success">' + data.message + '</p>';
        }} else {{
            console.error('[Login] Failed:', data.message);
            document.getElementById('login-feedback').innerHTML = 
                '<p class="error">' + data.message + '</p>';
        }}
    }} catch (err) {{
        console.error('[Login] Error:', err);
        document.getElementById('login-feedback').innerHTML = 
            `<p class="error">Connection error</p>`;
    }} finally {{
        btn.disabled = false;
        btn.textContent = 'Sign In';
    }}
}});

console.log('[Login] Page loaded');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def slow_page() -> str:
    """Slow loading page for timeout testing."""
    return f"""{get_page_header("Slow", "/slow")}
<div class="card">
    <h2>Slow Loading</h2>
    <p>This page simulates slow network responses. Use for testing timeouts and loading states.</p>
</div>

<div class="card">
    <h3>Simulated Delays</h3>
    <button onclick="fetchSlow('/api/slow?delay=1000')">Fast (1s)</button>
    <button onclick="fetchSlow('/api/slow?delay=3000')">Medium (3s)</button>
    <button onclick="fetchSlow('/api/slow?delay=10000')">Slow (10s)</button>
    <button onclick="fetchSlow('/api/slow?delay=500')">Very Fast (0.5s)</button>
</div>

<div class="card">
    <h3>Dynamic Content</h3>
    <div id="slow-content">
        <p>Click a button above to load content with a delay.</p>
    </div>
    <button onclick="clearContent()">Clear</button>
</div>

<div class="card">
    <h3>Request Status</h3>
    <pre id="request-status" style="background:#f5f5f5; padding:15px; border-radius:8px;">No requests made yet.</pre>
</div>

<script>
let requestCount = 0;

async function fetchSlow(url) {{
    requestCount++;
    const id = requestCount;
    const status = document.getElementById('request-status');
    const content = document.getElementById('slow-content');
    
    status.textContent = '[Request #' + id + '] Loading...';
    content.innerHTML = '<div class="loading">Loading...</div>';
    
    console.log('[Slow] Request #' + id + ' started:', url);
    
    const startTime = Date.now();
    
    try {{
        const response = await fetch(url);
        const data = await response.json();
        const elapsed = Date.now() - startTime;
        
        status.textContent = '[Request #' + id + '] Success! (' + elapsed + 'ms)\\n' + JSON.stringify(data, null, 2);
        content.innerHTML = '<div class="success"><p><strong>Loaded successfully!</strong></p><p>Delay requested: ' + data.delay + 'ms</p><p>Actual delay: ' + data.actual_delay + 'ms</p><p>Time elapsed: ' + elapsed + 'ms</p></div>';
        
        console.log('[Slow] Request #' + id + ' completed:', data);
        
    }} catch (err) {{
        const elapsed = Date.now() - startTime;
        status.textContent = '[Request #' + id + '] Error after ' + elapsed + 'ms: ' + err;
        content.innerHTML = '<div class="error"><p>Error: ' + err + '</p></div>';
        console.error('[Slow] Request #' + id + ' failed:', err);
    }}
}}

function clearContent() {{
    document.getElementById('slow-content').innerHTML = '<p>Cleared. Click a button to load.</p>';
    document.getElementById('request-status').textContent = 'No requests made yet.';
    console.log('[Slow] Content cleared');
}}

console.log('[Slow] Page loaded');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def error_page() -> str:
    """Error page for testing error handling."""
    return f"""{get_page_header("Error", "/error")}
<div class="card">
    <h2>Error Handling</h2>
    <p>This page simulates various HTTP error conditions.</p>
</div>

<div class="card">
    <h3>Simulate Errors</h3>
    <button onclick="triggerError('/api/error/404')">404 Not Found</button>
    <button onclick="triggerError('/api/error/500')">500 Server Error</button>
    <button onclick="triggerError('/api/error/timeout')">Timeout</button>
    <button onclick="triggerError('/api/error/slow')">Slow Response (5s)</button>
</div>

<div class="card">
    <h3>Console Error Test</h3>
    <button onclick="logError()">Log JavaScript Error</button>
    <button onclick="throwError()">Throw Uncaught Error</button>
    <button onclick="logWarning()">Log Warning</button>
    <button onclick="logObject()">Log Object</button>
</div>

<div class="card">
    <h3>Error Status</h3>
    <pre id="error-status" style="background:#f5f5f5; padding:15px; border-radius:8px;">No errors triggered yet.</pre>
</div>

<script>
function triggerError(url) {{
    const status = document.getElementById('error-status');
    status.textContent = '[Error] Fetching: ' + url;
    console.log('[Error] Triggering:', url);
    
    fetch(url)
        .then(response => {{
            if (!response.ok) {{
                throw new Error('HTTP ' + response.status + ': ' + response.statusText);
            }}
            return response.json();
        }})
        .then(data => {{
            status.textContent = '[Error] Success (unexpected): ' + JSON.stringify(data);
            console.log('[Error] Unexpected success:', data);
        }})
        .catch(err => {{
            status.textContent = '[Error] Caught: ' + err.message;
            console.error('[Error] Caught error:', err.message);
        }});
}}

function logError() {{
    console.error('[ErrorTest] This is a JavaScript error log');
    updateStatus('JavaScript error logged');
}}

function throwError() {{
    try {{
        throw new Error('[ErrorTest] This is a test error');
    }} catch (e) {{
        console.error('[ErrorTest] Caught:', e.message);
        updateStatus('Uncaught error thrown (caught by test)');
    }}
}}

function logWarning() {{
    console.warn('[ErrorTest] This is a warning');
    updateStatus('Warning logged');
}}

function logObject() {{
    const obj = {{
        name: 'TestObject',
        value: 42,
        nested: {{ a: 1, b: 2 }},
        array: [1, 2, 3]
    }};
    console.log('[ErrorTest] Logging object:', obj);
    console.log('[ErrorTest] Formatted:', JSON.stringify(obj, null, 2));
    updateStatus('Object logged to console');
}}

function updateStatus(msg) {{
    document.getElementById('error-status').textContent = '[Status] ' + msg;
}}

console.log('[Error] Page loaded');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""


def spa_page() -> str:
    """Single Page App simulation."""
    routes = {
        "/": {"title": "Home", "content": "Welcome to the SPA. Use navigation to explore."},
        "/about": {"title": "About", "content": "About page content goes here."},
        "/contact": {"title": "Contact", "content": "Contact form would be here."},
        "/profile": {"title": "Profile", "content": "User profile information."},
    }

    routes_html = ""
    for path, info in routes.items():
        routes_html += f'<li><a href="{path}" onclick="navigate(\'{path}\'); return false;">{info["title"]}</a></li>'

    return f"""{get_page_header("SPA", "/")}
<div class="card">
    <h2>Single Page App</h2>
    <p>This page demonstrates client-side routing (SPA behavior).</p>
</div>

<div style="display:flex; gap:20px;">
    <div class="card" style="width:200px;">
        <h4>Navigation</h4>
        <ul style="list-style:none; padding:0;">
            {routes_html}
        </ul>
    </div>
    
    <div class="card" style="flex:1;">
        <h3 id="spa-title">Home</h3>
        <div id="spa-content">
            <p>Welcome to the SPA. Use navigation to explore.</p>
            <p>Client-side routing simulates page navigation without server requests.</p>
        </div>
        
        <div id="spa-dynamic" style="margin-top:20px; padding:15px; background:#f5f5f5; border-radius:8px;">
            <h4>Dynamic Content</h4>
            <button onclick="loadDynamic('/api/spa/data1')">Load Data 1</button>
            <button onclick="loadDynamic('/api/spa/data2')">Load Data 2</button>
            <div id="dynamic-result" style="margin-top:10px;"></div>
        </div>
    </div>
</div>

<script>
const routes = {routes};

function navigate(path) {{
    console.log('[SPA] Navigating to:', path);
    
    const route = routes[path] || routes['/'];
    document.getElementById('spa-title').textContent = route.title;
    document.getElementById('spa-content').innerHTML = '<p>' + route.content + '</p>';
    
    // Update active nav
    document.querySelectorAll('nav a').forEach(a => {{
        a.classList.remove('active');
        if (a.getAttribute('href') === path) a.classList.add('active');
    }});
    
    // Track navigation
    window.__chaitya_test__.navigations.push({{
        path: path,
        title: route.title,
        timestamp: Date.now()
    }});
}}

async function loadDynamic(url) {{
    console.log('[SPA] Loading dynamic:', url);
    const result = document.getElementById('dynamic-result');
    result.innerHTML = '<div class="loading">Loading...</div>';
    
    try {{
        const response = await fetch(url);
        const data = await response.json();
        result.innerHTML = '<pre style="background:#fff; padding:10px; border-radius:4px;">' + JSON.stringify(data, null, 2) + '</pre>';
        console.log('[SPA] Dynamic loaded:', data);
    }} catch (err) {{
        result.innerHTML = '<div class="error">Error: ' + err + '</div>';
        console.error('[SPA] Dynamic load failed:', err);
    }}
}}

console.log('[SPA] Page loaded');
</script>
{get_page_footer()}
{CONSOLE_CAPTURE_JS}
{EVENT_TRACKING_JS}"""
