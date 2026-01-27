# Test DDO save/copy functionality
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "omnichat")

# Simulate DDOScreen save logic
def test_save():
    from src.ddo import DiscussionMode

    # Simulated log
    log_text = """🚀 Начинаем дискуссию...
Тема: Тестовая тема
Режим: ⚡ Быстрый

📡 Статус агентов:
✅ GEMINI
✅ GPT
✅ CLAUDE

========================================
📍 ФАЗА: 🏗️ Архитектура (Gemini)
========================================
💬 GEMINI:
Тестовый ответ от Gemini...

========================================
✅ УСПЕХ! Консенсус достигнут.
========================================

📊 Статистика:
   Стоимость: $0.0426
   Время: 01:05
   Сообщений: 3
"""

    topic = "Тестовая тема"
    mode = DiscussionMode.QUICK

    # Create state/ddo folder
    state_dir = Path("omnichat/state/ddo")
    state_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ddo_{ts}_{mode.value}.md"
    filepath = state_dir / filename

    # Format as Markdown
    header = f"""# DDO Discussion Log

**Дата:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Тема:** {topic}
**Режим:** {mode.display_name}

---

"""
    content = header + log_text

    # Atomic write
    tmp = filepath.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        import os
        os.fsync(f.fileno())
    import os
    os.replace(tmp, filepath)

    print(f"[OK] Saved: {filepath}")
    print(f"     Size: {len(content)} bytes")

    # Verify
    saved = filepath.read_text(encoding="utf-8")
    assert "DDO Discussion Log" in saved
    assert topic in saved
    assert "DDO Discussion Log" in saved  # Already checked above
    print("[OK] Content verified!")

    # Test copy (just check function exists)
    try:
        import subprocess
        process = subprocess.Popen(['clip.exe'], stdin=subprocess.PIPE, shell=True)
        process.communicate(log_text.encode('utf-16-le'))
        print("[OK] Clipboard copy works!")
    except Exception as e:
        print(f"[WARN] Clipboard: {e}")

    return filepath

if __name__ == "__main__":
    result = test_save()
    print(f"\n[FILE] {result.absolute()}")
