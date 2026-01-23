@echo off
echo Stopping all HOPE processes...
taskkill /F /IM python.exe 2>nul
echo Done.
pause
