"""
HOPE/NORE Control Panel

Simple GUI panel with action buttons.
Morning button triggers daily scan at any time.

Usage:
    from core.panel import ControlPanel

    panel = ControlPanel()
    panel.run()  # Opens GUI window

    # Or headless mode
    panel.execute_morning()
"""
from __future__ import annotations

import datetime
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

# Try to import tkinter (may not be available in all environments)
try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

from core.morning_scanner import MorningScanner, get_scanner


class ControlPanel:
    """
    HOPE/NORE Control Panel with action buttons.

    Buttons:
    - Morning: Execute morning scan
    - Status: Show system status
    - Stop: Emergency stop
    """

    def __init__(self):
        self.scanner = get_scanner()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_running = False
        self._window: Optional[tk.Tk] = None

    def execute_morning(self) -> str:
        """Execute morning scan (headless mode)."""
        print(f"[{datetime.datetime.now()}] Executing morning scan...")
        report = self.scanner.trigger_morning_scan()
        return report.summary()

    def start_scheduler(self) -> None:
        """Start background scheduler for 10:00 AM scan."""
        if self._scheduler_running:
            return

        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="MorningScanScheduler"
        )
        self._scheduler_thread.start()
        print("[Scheduler] Started - will run morning scan at 10:00 AM")

    def stop_scheduler(self) -> None:
        """Stop background scheduler."""
        self._scheduler_running = False
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=2)
        print("[Scheduler] Stopped")

    def _scheduler_loop(self) -> None:
        """Background loop checking for scheduled scan time."""
        last_scan_date: Optional[datetime.date] = None

        while self._scheduler_running:
            now = datetime.datetime.now()

            # Check if it's 10:00 AM and we haven't scanned today
            if (
                now.hour == 10 and
                now.minute == 0 and
                last_scan_date != now.date()
            ):
                print(f"[Scheduler] Triggering scheduled morning scan...")
                try:
                    self.scanner.trigger_morning_scan()
                    last_scan_date = now.date()
                except Exception as e:
                    print(f"[Scheduler] Error during scan: {e}")

            # Sleep for 30 seconds before next check
            time.sleep(30)

    def run(self) -> None:
        """Run GUI panel (blocking)."""
        if not TK_AVAILABLE:
            print("ERROR: tkinter not available. Use headless mode.")
            print("  panel.execute_morning()  # Run scan directly")
            return

        self._create_window()
        self.start_scheduler()

        try:
            self._window.mainloop()
        finally:
            self.stop_scheduler()

    def _create_window(self) -> None:
        """Create tkinter window with buttons."""
        self._window = tk.Tk()
        self._window.title("HOPE/NORE Control Panel")
        self._window.geometry("600x500")
        self._window.configure(bg="#1a1a2e")

        # Title
        title_label = tk.Label(
            self._window,
            text="HOPE/NORE Control Panel",
            font=("Consolas", 16, "bold"),
            fg="#00ff88",
            bg="#1a1a2e"
        )
        title_label.pack(pady=15)

        # Button frame
        btn_frame = tk.Frame(self._window, bg="#1a1a2e")
        btn_frame.pack(pady=10)

        # Morning button (main action)
        morning_btn = tk.Button(
            btn_frame,
            text="UTRO",
            font=("Consolas", 14, "bold"),
            width=15,
            height=2,
            bg="#00aa55",
            fg="white",
            activebackground="#00cc66",
            command=self._on_morning_click
        )
        morning_btn.grid(row=0, column=0, padx=10, pady=5)

        # Status button
        status_btn = tk.Button(
            btn_frame,
            text="STATUS",
            font=("Consolas", 14),
            width=15,
            height=2,
            bg="#0066aa",
            fg="white",
            activebackground="#0088cc",
            command=self._on_status_click
        )
        status_btn.grid(row=0, column=1, padx=10, pady=5)

        # Stop button
        stop_btn = tk.Button(
            btn_frame,
            text="STOP",
            font=("Consolas", 14, "bold"),
            width=15,
            height=2,
            bg="#aa3333",
            fg="white",
            activebackground="#cc4444",
            command=self._on_stop_click
        )
        stop_btn.grid(row=0, column=2, padx=10, pady=5)

        # Output area
        output_label = tk.Label(
            self._window,
            text="Output:",
            font=("Consolas", 10),
            fg="#888888",
            bg="#1a1a2e",
            anchor="w"
        )
        output_label.pack(fill="x", padx=15)

        self._output_text = scrolledtext.ScrolledText(
            self._window,
            font=("Consolas", 9),
            bg="#0d0d1a",
            fg="#00ff88",
            height=20,
            wrap=tk.WORD
        )
        self._output_text.pack(fill="both", expand=True, padx=15, pady=10)

        # Status bar
        self._status_var = tk.StringVar()
        self._status_var.set("Ready | Scheduler: Active | Next scan: 10:00 AM")
        status_bar = tk.Label(
            self._window,
            textvariable=self._status_var,
            font=("Consolas", 9),
            fg="#666666",
            bg="#1a1a2e",
            anchor="w"
        )
        status_bar.pack(fill="x", padx=15, pady=5)

    def _on_morning_click(self) -> None:
        """Handle Morning button click."""
        self._log("Starting morning scan...")
        self._status_var.set("Running morning scan...")
        self._window.update()

        try:
            report = self.scanner.trigger_morning_scan()
            self._log(report.summary())
            self._status_var.set(f"Scan complete | Files: {report.total_files} | Errors: {report.error_count}")
        except Exception as e:
            self._log(f"ERROR: {e}")
            self._status_var.set(f"Error during scan: {e}")

    def _on_status_click(self) -> None:
        """Handle Status button click."""
        last_report = self.scanner.get_last_report()

        if last_report:
            self._log(f"\n=== Last Scan Status ===")
            self._log(f"Time: {last_report.scan_time}")
            self._log(f"Files: {last_report.total_files}")
            self._log(f"OK: {last_report.ok_count}")
            self._log(f"Warnings: {last_report.warning_count}")
            self._log(f"Errors: {last_report.error_count}")
            self._log(f"Archived: {len(last_report.archived_files)}")
        else:
            self._log("No scan performed yet. Click 'UTRO' to run scan.")

        self._log(f"\nScheduler: {'Active' if self._scheduler_running else 'Stopped'}")
        self._log(f"Current time: {datetime.datetime.now().strftime('%H:%M:%S')}")

    def _on_stop_click(self) -> None:
        """Handle Stop button click."""
        if TK_AVAILABLE:
            if messagebox.askyesno("Confirm", "Emergency STOP - Are you sure?"):
                self._log("!!! EMERGENCY STOP TRIGGERED !!!")
                self._status_var.set("STOPPED")
                self.stop_scheduler()

                # Create STOP.flag
                stop_flag = Path(r'C:\Users\kirillDev\Desktop\TradingBot\minibot\STOP.flag')
                stop_flag.write_text(
                    f"STOP triggered at {datetime.datetime.now().isoformat()}\n"
                    f"Reason: Manual stop from Control Panel\n",
                    encoding="utf-8"
                )
                self._log(f"STOP.flag created at {stop_flag}")

    def _log(self, message: str) -> None:
        """Add message to output area."""
        if hasattr(self, '_output_text'):
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self._output_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self._output_text.see(tk.END)
        else:
            print(message)


def launch_panel() -> None:
    """Launch control panel (convenience function)."""
    panel = ControlPanel()
    panel.run()


if __name__ == "__main__":
    launch_panel()
