# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T01:55:00Z
# Purpose: Patch hope_dashboard.py to add Guardian support
# === END SIGNATURE ===
"""
Patch to add Position Guardian panel to HOPE Dashboard.

Usage:
    python tools/patch_dashboard_guardian.py

This adds:
1. Guardian API proxy endpoints (/api/guardian/*)
2. Guardian panel HTML
3. Guardian JavaScript functions
"""

import re
from pathlib import Path

DASHBOARD_PATH = Path(__file__).parent.parent / "scripts" / "hope_dashboard.py"

# ============================================================================
# GUARDIAN PANEL HTML (to insert into HTML_TEMPLATE)
# ============================================================================

GUARDIAN_PANEL_HTML = '''
    <!-- Position Guardian Panel -->
    <div class="card" id="guardian-panel">
        <div class="card-header">
            <h3>🛡️ Position Guardian</h3>
            <span id="guardian-status" class="badge">LOADING</span>
            <button class="btn btn-sm" onclick="refreshGuardian()">↻</button>
        </div>
        <div class="card-body">
            <div class="guardian-config" id="guardian-config">
                <span>Hard SL: <b id="guardian-sl">-2%</b></span>
                <span>Base TP: <b id="guardian-tp">1.5%</b></span>
                <span>Trailing: <b id="guardian-trailing">✅</b></span>
            </div>
            <div class="guardian-positions" id="guardian-positions">
                <div class="loading">Loading positions...</div>
            </div>
            <div class="guardian-controls">
                <button class="btn btn-success btn-sm" onclick="guardianStart()">▶️ Start</button>
                <button class="btn btn-danger btn-sm" onclick="guardianStop()">⏹️ Stop</button>
                <button class="btn btn-primary btn-sm" onclick="guardianSync()">🔄 Sync</button>
                <button class="btn btn-warning btn-sm" onclick="guardianRunOnce()">🧪 Run Once</button>
            </div>
        </div>
    </div>
'''

GUARDIAN_STYLES = '''
    /* Guardian Panel Styles */
    #guardian-panel {
        background: linear-gradient(135deg, #1e3a5f 0%, #0d2137 100%);
        border: 1px solid #00d9ff;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 20px;
    }

    #guardian-panel .card-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 15px;
    }

    #guardian-panel .badge {
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.85em;
    }

    #guardian-panel .badge.running { background: #00ff88; color: #000; }
    #guardian-panel .badge.stopped { background: #ff4444; color: #fff; }

    .guardian-config {
        display: flex;
        gap: 20px;
        margin-bottom: 15px;
        color: #aaa;
    }

    .guardian-positions {
        background: rgba(0,0,0,0.2);
        border-radius: 8px;
        padding: 10px;
        margin-bottom: 15px;
        max-height: 200px;
        overflow-y: auto;
    }

    .guardian-position {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid rgba(255,255,255,0.1);
    }

    .guardian-position:last-child { border-bottom: none; }

    .guardian-position .symbol { font-weight: bold; }
    .guardian-position .pnl.positive { color: #00ff88; }
    .guardian-position .pnl.negative { color: #ff4444; }

    .guardian-controls {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
    }

    .btn-sm {
        padding: 6px 12px;
        font-size: 0.85em;
    }
'''

GUARDIAN_JS = '''
    // Guardian Functions
    async function refreshGuardian() {
        try {
            const resp = await fetch('/api/guardian/status');
            const data = await resp.json();

            // Update status badge
            const badge = document.getElementById('guardian-status');
            if (data.running) {
                badge.textContent = '🟢 RUNNING';
                badge.className = 'badge running';
            } else {
                badge.textContent = '🔴 STOPPED';
                badge.className = 'badge stopped';
            }

            // Update config
            const config = data.config || {};
            document.getElementById('guardian-sl').textContent = (config.hard_sl || -2) + '%';
            document.getElementById('guardian-tp').textContent = (config.base_tp || 1.5) + '%';
            document.getElementById('guardian-trailing').textContent = config.trailing_enabled ? '✅' : '❌';

            // Update positions
            const posDiv = document.getElementById('guardian-positions');
            const positions = data.positions_detail || [];

            if (positions.length === 0) {
                posDiv.innerHTML = '<div class="no-positions">No positions tracked</div>';
            } else {
                let html = '';
                let totalValue = 0;
                let totalPnl = 0;

                positions.forEach(p => {
                    const pnlClass = p.pnl_pct >= 0 ? 'positive' : 'negative';
                    const pnlSign = p.pnl_pct >= 0 ? '+' : '';
                    totalValue += p.value_usd || 0;
                    totalPnl += (p.value_usd || 0) * (p.pnl_pct || 0) / 100;

                    html += `
                        <div class="guardian-position">
                            <span class="symbol">${p.symbol}</span>
                            <span>$${p.entry_price?.toFixed(4)} → $${p.current_price?.toFixed(4)}</span>
                            <span class="pnl ${pnlClass}">${pnlSign}${p.pnl_pct?.toFixed(2)}%</span>
                            <span>$${(p.value_usd || 0).toFixed(2)}</span>
                            <button class="btn btn-danger btn-xs" onclick="guardianClose('${p.symbol}')">✕</button>
                        </div>
                    `;
                });

                html += `
                    <div class="guardian-total">
                        <b>Total: $${totalValue.toFixed(2)} (${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)})</b>
                    </div>
                `;

                posDiv.innerHTML = html;
            }
        } catch (e) {
            console.error('Guardian refresh error:', e);
        }
    }

    async function guardianStart() {
        await fetch('/api/guardian/start', { method: 'POST' });
        await refreshGuardian();
    }

    async function guardianStop() {
        await fetch('/api/guardian/stop', { method: 'POST' });
        await refreshGuardian();
    }

    async function guardianSync() {
        await fetch('/api/guardian/sync', { method: 'POST' });
        await refreshGuardian();
    }

    async function guardianRunOnce() {
        const resp = await fetch('/api/guardian/run-once', { method: 'POST' });
        const data = await resp.json();
        alert(`Checked: ${data.checked}, Closed: ${data.closed}`);
        await refreshGuardian();
    }

    async function guardianClose(symbol) {
        if (confirm(`Close ${symbol} position?`)) {
            await fetch(`/api/guardian/close/${symbol}`, { method: 'POST' });
            await refreshGuardian();
        }
    }

    // Auto-refresh guardian every 30 seconds
    setInterval(refreshGuardian, 30000);

    // Initial load
    setTimeout(refreshGuardian, 1000);
'''

# Handler code to add Guardian proxy
GUARDIAN_HANDLER_CODE = '''
        # Guardian API proxy to hope-core (port 8201)
        elif self.path.startswith('/api/guardian/'):
            import urllib.request
            import urllib.error
            try:
                # Proxy to hope-core
                hope_core_url = f"http://127.0.0.1:8201{self.path}"
                with urllib.request.urlopen(hope_core_url, timeout=10) as response:
                    data = response.read()
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.URLError as e:
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
'''

GUARDIAN_POST_HANDLER_CODE = '''
        # Guardian POST API proxy
        elif self.path.startswith('/api/guardian/'):
            import urllib.request
            import urllib.error
            try:
                hope_core_url = f"http://127.0.0.1:8201{self.path}"
                req = urllib.request.Request(hope_core_url, method='POST')
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = response.read()
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.URLError as e:
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
'''


def patch_dashboard():
    """Apply Guardian patch to dashboard."""

    if not DASHBOARD_PATH.exists():
        print(f"ERROR: Dashboard not found at {DASHBOARD_PATH}")
        return False

    content = DASHBOARD_PATH.read_text(encoding='utf-8')

    # Check if already patched
    if 'guardian-panel' in content:
        print("Dashboard already has Guardian panel")
        return True

    # 1. Add Guardian styles to CSS
    # Find end of style block
    style_marker = '</style>'
    if style_marker in content:
        content = content.replace(style_marker, GUARDIAN_STYLES + '\n    ' + style_marker)
        print("✅ Added Guardian styles")

    # 2. Add Guardian panel HTML after controls div
    controls_end = '</div>  <!-- end controls -->'
    alt_controls_end = '<div class="metrics">'

    if controls_end in content:
        content = content.replace(controls_end, controls_end + '\n' + GUARDIAN_PANEL_HTML)
        print("✅ Added Guardian panel HTML")
    elif alt_controls_end in content:
        content = content.replace(alt_controls_end, GUARDIAN_PANEL_HTML + '\n    ' + alt_controls_end)
        print("✅ Added Guardian panel HTML (alt)")
    else:
        # Find a good place - after header
        header_end = '<div class="metrics">'
        if header_end in content:
            content = content.replace(header_end, GUARDIAN_PANEL_HTML + '\n    ' + header_end)
            print("✅ Added Guardian panel HTML (header)")

    # 3. Add Guardian JavaScript
    script_end = '</script>'
    if script_end in content:
        # Add before last </script>
        last_script = content.rfind(script_end)
        content = content[:last_script] + GUARDIAN_JS + '\n    ' + content[last_script:]
        print("✅ Added Guardian JavaScript")

    # 4. Add Guardian GET handler
    # Find do_GET method and add guardian handler
    do_get_marker = "elif self.path == '/api/metrics':"
    if do_get_marker in content:
        content = content.replace(do_get_marker, GUARDIAN_HANDLER_CODE + '\n        ' + do_get_marker)
        print("✅ Added Guardian GET handler")

    # 5. Add Guardian POST handler
    do_post_marker = "elif self.path == '/api/stop':"
    if do_post_marker in content:
        content = content.replace(do_post_marker, GUARDIAN_POST_HANDLER_CODE + '\n        ' + do_post_marker)
        print("✅ Added Guardian POST handler")

    # Write patched content
    backup_path = DASHBOARD_PATH.with_suffix('.py.bak')
    DASHBOARD_PATH.rename(backup_path)
    print(f"✅ Backup created: {backup_path}")

    DASHBOARD_PATH.write_text(content, encoding='utf-8')
    print(f"✅ Dashboard patched: {DASHBOARD_PATH}")

    return True


if __name__ == '__main__':
    print("=" * 60)
    print("HOPE Dashboard Guardian Patcher")
    print("=" * 60)

    success = patch_dashboard()

    if success:
        print("\n✅ DONE! Restart dashboard to apply changes:")
        print("   systemctl restart hope-dashboard")
    else:
        print("\n❌ FAILED")
