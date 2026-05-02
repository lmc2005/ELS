@echo off
echo === English Learning System ===
echo.

echo [1/2] Starting backend...
cd /d "%~dp0backend"
if not exist venv (
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt -q
) else (
    call venv\Scripts\activate.bat
)
start "ELS Backend" uvicorn app.main:app --host 127.0.0.1 --port 8000
echo   Backend: http://127.0.0.1:8000

cd /d "%~dp0frontend"
if not exist node_modules (
    npm install --silent
)
start "ELS Frontend" npx vite --host 127.0.0.1 --port 5173
echo   Frontend: http://127.0.0.1:5173

echo.
echo === Ready ===
echo Open http://127.0.0.1:5173 in your browser.
pause
