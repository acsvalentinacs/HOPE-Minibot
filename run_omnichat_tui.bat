@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting HOPE OMNI-CHAT TUI...
set PYTHONIOENCODING=utf-8
python -c "import sys; sys.path.insert(0, 'omnichat'); from app import HopeOmniChat; HopeOmniChat().run()"
pause
