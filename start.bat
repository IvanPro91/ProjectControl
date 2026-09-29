@echo off
rem ProjectControl launcher. Keeps the window open on errors.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pydantic" 2>nul || (
    echo Installing dependencies, please wait...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :error
)

echo Starting ProjectControl...
echo.
".venv\Scripts\python.exe" run.py %*
set CODE=%ERRORLEVEL%

if not "%CODE%"=="0" (
    echo.
    echo ProjectControl stopped with error code %CODE%.
    goto :error
)
goto :eof

:error
echo.
echo Could not start ProjectControl.
echo Common reasons:
echo   - another app is already using the port (run: python run.py --port 8780)
echo   - Python 3.10+ is missing from PATH
echo.
pause
