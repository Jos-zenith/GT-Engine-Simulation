@echo off
cd /d "%~dp0\.."
echo Open http://127.0.0.1:8000 in your browser after the server starts.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\phase1_server.py
) else (
  echo For ML support install requirements-ml.txt in your Python environment.
  python src\phase1_server.py
)
pause
