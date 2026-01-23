@echo off
title HOPE Supervisor
cd /d "%~dp0"
echo Starting HOPE Supervisor...
"C:\Users\kirillDev\AppData\Local\Programs\Python\Python312\python.exe" -m tools.supervisor
pause
