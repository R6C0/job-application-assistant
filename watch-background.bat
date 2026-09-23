@echo off
REM Background loop, every 90 minutes, quiet 22:00-07:00.
REM Used by the scheduled task. Logs to jobhunt.log.
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" -m jobhunt.cli watch >> jobhunt.log 2>&1
