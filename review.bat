@echo off
REM Opens the review interface at http://127.0.0.1:8765
cd /d "%~dp0"
if not exist "web\dist\index.html" (
  echo Frontend not built. Building it now, this takes about 30 seconds...
  pushd web
  call npm install
  call npm run build
  popd
)
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" -m jobhunt.cli review
