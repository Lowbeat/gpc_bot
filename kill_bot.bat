@echo off
chcp 65001 >nul
echo Killing bot processes...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *bot.py*" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *whoami.py*" 2>nul
timeout /t 1 /nobreak >nul 2>&1
echo Done.