@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Quiz Solver
echo ============================================
echo.

REM ---- Check Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install from https://www.python.org/downloads/
    echo         and check "Add python.exe to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ---- Create virtual environment if missing ----
if not exist ".venv\Scripts\activate.bat" (
    echo [Setup] Creating virtual environment...
    python -m venv .venv
)
call ".venv\Scripts\activate.bat"

REM ---- Ensure dependencies are installed (self-healing) ----
REM If any required module is missing, (re)install everything.
python -c "import dotenv, playwright, google.genai" >nul 2>&1
if errorlevel 1 (
    echo [Setup] Installing required libraries. This may take a few minutes...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install libraries. Check your internet connection.
        pause
        exit /b 1
    )
    echo [Setup] Installing browser (Chromium)...
    python -m playwright install chromium
    echo [Setup] Done.
    echo.
)

REM ---- Check .env ----
if not exist ".env" (
    echo [NOTE] ".env" file not found.
    echo        Copy ".env.example" to ".env" and set your API key / login info.
    echo.
    pause
    exit /b 1
)

REM ---- Run the tool (pass through any arguments) ----
python solve.py %*

echo.
pause
