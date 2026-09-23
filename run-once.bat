@echo off
REM One pass: fetch, score, research, draft, notify. Nothing is submitted.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jobhunt.cli run %*
pause
