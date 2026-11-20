@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting whoami (shows chat IDs)...
echo Write something in target chat/channel to see its ID.
echo Press Ctrl+C to stop.
python whoami.py