@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  HOPE OMNI-CHAT v1.5
echo ========================================
echo.

REM Clear Python cache
for /d /r "omnichat" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

set PYTHONIOENCODING=utf-8
python -c "import sys; sys.path.insert(0, 'omnichat'); from app import HopeOmniChat; HopeOmniChat().run()"

if errorlevel 1 (
    echo.
    echo ERROR: Failed to start. Check Python installation.
    pause
)
