@echo off
REM Opens the review queue at http://127.0.0.1:8765
cd /d "%~dp0"
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" -m jobhunt.cli review
