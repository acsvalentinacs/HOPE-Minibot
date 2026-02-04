# === AI SIGNATURE ===
# Module: scripts/sauce_command.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05 00:10:00 UTC
# Purpose: Secret Sauce Telegram command handler
# === END SIGNATURE ===
"""
Secret Sauce command for Telegram bot.

Usage: Copy cmd_sauce method into tg_bot_simple.py
"""

# Add this import at top of tg_bot_simple.py if not present:
# import aiohttp

SAUCE_COMMAND_CODE = '''
    async def cmd_sauce(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Secret Sauce status - /sauce"""
        if not await self._guard_admin(update):
            return

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8201/api/secret-sauce", timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        panic = data.get("panic", {})
                        time_f = data.get("time", {})

                        panic_icon = "🔴" if panic.get("panic_mode") else "🟢"
                        time_icon = "🟢" if time_f.get("allowed") else "🔴"

                        lines = [
                            "🧠 <b>SECRET SAUCE STATUS</b>",
                            "",
                            f'{panic_icon} <b>Panic:</b> {"ACTIVE - " + str(panic.get("panic_reason")) if panic.get("panic_mode") else "OK"}',
                            f'📊 <b>Daily PnL:</b> ${panic.get("daily_pnl", 0):.2f}',
                            f'⚡ <b>Circuit Trips:</b> {panic.get("circuit_trips", 0)}',
                            "",
                            f'{time_icon} <b>Time Filter:</b> {time_f.get("reason", "?")}',
                            f'🚫 <b>Blackouts:</b> {", ".join(time_f.get("windows", []))}',
                            "",
                            f'📈 <b>Adaptive Threshold:</b> {data.get("threshold", 0.35):.0%}',
                            "",
                            "🏆 <b>Top Symbols:</b>",
                        ]

                        top = data.get("top_symbols", [])
                        if top:
                            for sym, pnl in top[:5]:
                                lines.append(f"  • {sym}: ${pnl:.2f}")
                        else:
                            lines.append("  (пока нет данных)")

                        blacklist = data.get("blacklist", [])
                        if blacklist:
                            lines.append(f"")
                            lines.append(f'🚫 <b>Blacklist:</b> {", ".join(blacklist)}')

                        shadow = data.get("shadow", {})
                        if shadow.get("total_trades", 0) > 0:
                            lines.append("")
                            lines.append("👻 <b>Shadow Mode:</b>")
                            lines.append(f'  Trades: {shadow.get("total_trades")} | WR: {shadow.get("win_rate", 0):.0%} | PnL: ${shadow.get("total_pnl", 0):.2f}')

                        await self._reply(update, "\\n".join(lines), parse_mode="HTML")
                    else:
                        await self._reply(update, "❌ HOPE Core не отвечает")
        except Exception as e:
            await self._reply(update, f"❌ Ошибка: {e}")
'''

# Morning report addition - add to existing morning report
MORNING_SAUCE_SECTION = '''
        # Secret Sauce section for morning report
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8201/api/secret-sauce", timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        panic = data.get("panic", {})
                        sauce_lines = [
                            "",
                            "🧠 SECRET SAUCE:",
                            f'  Threshold: {data.get("threshold", 0.35):.0%}',
                            f'  Panic: {"🔴 " + panic.get("panic_reason") if panic.get("panic_mode") else "🟢 OK"}',
                            f'  Daily PnL: ${panic.get("daily_pnl", 0):.2f}',
                        ]
                        top = data.get("top_symbols", [])[:3]
                        if top:
                            sauce_lines.append(f'  Top: {", ".join([s[0] for s in top])}')
                        blacklist = data.get("blacklist", [])
                        if blacklist:
                            sauce_lines.append(f'  Blacklist: {", ".join(blacklist)}')
                        report += "\\n".join(sauce_lines)
        except:
            pass
'''

if __name__ == "__main__":
    print("=== SAUCE COMMAND CODE ===")
    print(SAUCE_COMMAND_CODE)
    print("\n=== MORNING SECTION ===")
    print(MORNING_SAUCE_SECTION)
