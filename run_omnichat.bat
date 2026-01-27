@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting HOPE OMNI-CHAT...
python omnichat/test_ddo_cli.py
pause
