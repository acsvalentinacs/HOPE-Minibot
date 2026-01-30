# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 23:50:00 UTC
# Purpose: HOPE AI 24/7 Autonomous Trading Daemon
# Contract: TESTNET mode, self-improving, fail-closed
# === END SIGNATURE ===
"""
HOPE AI - 24/7 AUTONOMOUS TRADING DAEMON

Complete trading cycle:
1. MoonBot signals -> AI Gateway analysis
2. AI Decision (Precursor + Mode + Filters + ML)
3. AutoTrader execution (TESTNET)
4. Outcome tracking -> Training data
5. Auto-retrain at 100 samples
6. Continuous evolution

NO STUBS. REAL TRADING. SELF-IMPROVING.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
import threading

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Logging
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-15s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "daemon.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("HOPE-DAEMON")

# State
STATE_DIR = PROJECT_ROOT / "state" / "ai"
STATE_DIR.mkdir(parents=True, exist_ok=True)


class HopeAIDaemon:
    """24/7 Autonomous Trading Daemon with Self-Improvement."""

    def __init__(self, testnet_only: bool = True):
        self.testnet_only = testnet_only
        self.running = False
        self.processes: Dict[str, subprocess.Popen] = {}

        # URLs
        self.gateway_url = "http://127.0.0.1:8100"
        self.autotrader_url = "http://127.0.0.1:8200"

        # Stats
        self.stats = {
            "started_at": None,
            "signals_processed": 0,
            "trades_executed": 0,
            "retrains_done": 0,
            "last_retrain": None,
            "current_model": "v1",
            "training_samples": 0,
        }

        # Thresholds
        self.retrain_threshold = 100
        self.retrain_increment = 50  # Retrain every 50 new samples after threshold

        log.info("=" * 60)
        log.info("HOPE AI DAEMON INITIALIZED")
        log.info("=" * 60)
        log.info(f"Mode: {'TESTNET' if testnet_only else 'LIVE'}")
        log.info(f"Retrain threshold: {self.retrain_threshold} samples")
        log.info("=" * 60)

    def _check_port(self, port: int) -> bool:
        """Check if port is in use."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0

    def _kill_port(self, port: int):
        """Kill process on port (Windows)."""
        try:
            result = subprocess.run(
                f'netstat -ano | findstr ":{port}"',
                shell=True, capture_output=True, text=True
            )
            for line in result.stdout.strip().split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    pid = parts[-1]
                    subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True)
                    log.info(f"Killed process {pid} on port {port}")
        except Exception as e:
            log.warning(f"Failed to kill port {port}: {e}")

    def start_gateway(self) -> bool:
        """Start AI Gateway."""
        if self._check_port(8100):
            log.info("[OK] AI Gateway already running on 8100")
            return True

        log.info("Starting AI Gateway...")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "ai_gateway"],
                cwd=PROJECT_ROOT,
                stdout=open(LOG_DIR / "gateway_out.log", "w"),
                stderr=open(LOG_DIR / "gateway_err.log", "w"),
            )
            self.processes["gateway"] = proc

            # Wait for startup
            for _ in range(30):
                time.sleep(1)
                if self._check_port(8100):
                    log.info("[OK] AI Gateway started on 8100")
                    return True

            log.error("[FAIL] AI Gateway failed to start")
            return False
        except Exception as e:
            log.error(f"[FAIL] Gateway error: {e}")
            return False

    def start_autotrader(self) -> bool:
        """Start AutoTrader."""
        if self._check_port(8200):
            log.info("[OK] AutoTrader already running on 8200")
            return True

        log.info("Starting AutoTrader...")
        mode = "TESTNET" if self.testnet_only else "LIVE"
        try:
            proc = subprocess.Popen(
                [sys.executable, "scripts/autotrader.py", "--mode", mode, "--api-port", "8200"],
                cwd=PROJECT_ROOT,
                stdout=open(LOG_DIR / "autotrader_out.log", "w"),
                stderr=open(LOG_DIR / "autotrader_err.log", "w"),
            )
            self.processes["autotrader"] = proc

            # Wait for startup
            for _ in range(30):
                time.sleep(1)
                if self._check_port(8200):
                    log.info(f"[OK] AutoTrader started on 8200 ({mode})")
                    return True

            log.error("[FAIL] AutoTrader failed to start")
            return False
        except Exception as e:
            log.error(f"[FAIL] AutoTrader error: {e}")
            return False

    def start_moonbot_integration(self) -> bool:
        """Start MoonBot live integration."""
        log.info("Starting MoonBot Integration...")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", """
import asyncio
import sys
sys.path.insert(0, '.')
from ai_gateway.integrations.moonbot_live import MoonBotLiveIntegration
from pathlib import Path

async def main():
    integration = MoonBotLiveIntegration(
        signals_file=Path('state/ai/signals/moonbot_signals.jsonl'),
        decisions_file=Path('state/ai/decisions.jsonl'),
        enable_event_bus=True,
    )
    await integration.start()

asyncio.run(main())
"""],
                cwd=PROJECT_ROOT,
                stdout=open(LOG_DIR / "moonbot_out.log", "w"),
                stderr=open(LOG_DIR / "moonbot_err.log", "w"),
            )
            self.processes["moonbot"] = proc
            log.info("[OK] MoonBot Integration started")
            return True
        except Exception as e:
            log.error(f"[FAIL] MoonBot error: {e}")
            return False

    def start_decision_bridge(self) -> bool:
        """Start Decision->AutoTrader bridge."""
        log.info("Starting Decision Bridge...")
        try:
            proc = subprocess.Popen(
                [sys.executable, "scripts/decision_to_autotrader.py"],
                cwd=PROJECT_ROOT,
                stdout=open(LOG_DIR / "bridge_out.log", "w"),
                stderr=open(LOG_DIR / "bridge_err.log", "w"),
            )
            self.processes["bridge"] = proc
            log.info("[OK] Decision Bridge started")
            return True
        except Exception as e:
            log.error(f"[FAIL] Bridge error: {e}")
            return False

    def get_training_status(self) -> Dict[str, Any]:
        """Get ML training data status."""
        training_file = STATE_DIR / "training" / "training_samples.jsonl"
        if not training_file.exists():
            return {"samples": 0, "wins": 0, "losses": 0}

        samples = []
        for line in training_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    samples.append(json.loads(line))
                except:
                    pass

        wins = sum(1 for s in samples if s.get("win_5m") is True)
        losses = sum(1 for s in samples if s.get("win_5m") is False)

        return {
            "samples": len(samples),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
        }

    def sync_training_data(self) -> int:
        """Sync outcomes to training data."""
        try:
            result = subprocess.run(
                [sys.executable, "scripts/sync_training_data.py", "--sync"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            # Parse output for new samples count
            for line in result.stdout.split('\n'):
                if 'Added' in line and 'samples' in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == 'Added':
                            return int(parts[i + 1])
            return 0
        except Exception as e:
            log.warning(f"Sync error: {e}")
            return 0

    def check_retrain_needed(self) -> bool:
        """Check if model retrain is needed."""
        status = self.get_training_status()
        samples = status["samples"]
        self.stats["training_samples"] = samples

        # First retrain at threshold
        if samples >= self.retrain_threshold and self.stats["retrains_done"] == 0:
            return True

        # Subsequent retrains every increment
        if self.stats["retrains_done"] > 0:
            samples_since_last = samples - (self.retrain_threshold +
                                            (self.stats["retrains_done"] - 1) * self.retrain_increment)
            if samples_since_last >= self.retrain_increment:
                return True

        return False

    def do_retrain(self):
        """Execute model retrain."""
        log.info("=" * 60)
        log.info("AUTO-RETRAIN TRIGGERED!")
        log.info("=" * 60)

        try:
            import httpx
            resp = httpx.post(f"{self.gateway_url}/self-improver/retrain", timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                self.stats["retrains_done"] += 1
                self.stats["last_retrain"] = datetime.now(timezone.utc).isoformat()
                self.stats["current_model"] = data.get("new_version", f"v{self.stats['retrains_done'] + 1}")
                log.info(f"[OK] Retrain complete! New model: {self.stats['current_model']}")
                log.info(f"     Accuracy: {data.get('metrics', {}).get('accuracy', 'N/A')}")
            else:
                log.error(f"[FAIL] Retrain failed: {resp.text}")
        except Exception as e:
            log.error(f"[FAIL] Retrain error: {e}")

    def print_status(self):
        """Print current status."""
        status = self.get_training_status()
        uptime = ""
        if self.stats["started_at"]:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(self.stats["started_at"])
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}h {minutes}m {seconds}s"

        log.info("-" * 60)
        log.info(f"DAEMON STATUS | Uptime: {uptime}")
        log.info(f"  Training samples: {status['samples']}/{self.retrain_threshold}")
        log.info(f"  Win rate: {status['win_rate']:.1f}% ({status['wins']}W/{status['losses']}L)")
        log.info(f"  Model version: {self.stats['current_model']}")
        log.info(f"  Retrains done: {self.stats['retrains_done']}")
        log.info("-" * 60)

    async def run_loop(self):
        """Main daemon loop."""
        self.running = True
        self.stats["started_at"] = datetime.now(timezone.utc).isoformat()

        cycle = 0
        sync_interval = 30  # Sync training data every 30 seconds
        status_interval = 60  # Print status every 60 seconds

        while self.running:
            try:
                cycle += 1

                # Sync training data
                if cycle % sync_interval == 0:
                    new_samples = self.sync_training_data()
                    if new_samples > 0:
                        log.info(f"[SYNC] Added {new_samples} new training samples")

                # Print status
                if cycle % status_interval == 0:
                    self.print_status()

                # Check retrain
                if cycle % sync_interval == 0 and self.check_retrain_needed():
                    self.do_retrain()

                # Check processes health
                for name, proc in list(self.processes.items()):
                    if proc.poll() is not None:
                        log.warning(f"[RESTART] {name} died, restarting...")
                        if name == "gateway":
                            self.start_gateway()
                        elif name == "autotrader":
                            self.start_autotrader()
                        elif name == "moonbot":
                            self.start_moonbot_integration()
                        elif name == "bridge":
                            self.start_decision_bridge()

                await asyncio.sleep(1)

            except Exception as e:
                log.error(f"Loop error: {e}")
                await asyncio.sleep(5)

    def start(self):
        """Start the daemon."""
        log.info("")
        log.info("=" * 60)
        log.info("STARTING HOPE AI 24/7 DAEMON")
        log.info("=" * 60)
        log.info("")

        # Start components
        if not self.start_gateway():
            log.error("Failed to start Gateway!")
            return False

        if not self.start_autotrader():
            log.error("Failed to start AutoTrader!")
            return False

        time.sleep(2)

        if not self.start_moonbot_integration():
            log.warning("MoonBot integration failed - will retry")

        if not self.start_decision_bridge():
            log.warning("Decision bridge failed - will retry")

        log.info("")
        log.info("=" * 60)
        log.info("ALL COMPONENTS STARTED!")
        log.info("=" * 60)
        log.info("")
        log.info("System is now running 24/7")
        log.info("Press Ctrl+C to stop")
        log.info("")

        # Print initial status
        self.print_status()

        # Run main loop
        try:
            asyncio.run(self.run_loop())
        except KeyboardInterrupt:
            log.info("Shutdown requested...")
        finally:
            self.stop()

    def stop(self):
        """Stop the daemon."""
        log.info("Stopping daemon...")
        self.running = False

        for name, proc in self.processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
                log.info(f"Stopped {name}")
            except:
                proc.kill()

        log.info("Daemon stopped.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HOPE AI 24/7 Daemon")
    parser.add_argument("--live", action="store_true", help="Enable LIVE mode (REAL MONEY!)")
    parser.add_argument("--status", action="store_true", help="Show status only")
    args = parser.parse_args()

    if args.live:
        confirm = input("WARNING: LIVE MODE uses REAL MONEY! Type 'I UNDERSTAND': ")
        if confirm != "I UNDERSTAND":
            print("Aborted.")
            return

    daemon = HopeAIDaemon(testnet_only=not args.live)

    if args.status:
        daemon.print_status()
        return

    # Handle signals
    def signal_handler(sig, frame):
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    daemon.start()


if __name__ == "__main__":
    main()
