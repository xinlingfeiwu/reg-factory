@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   reg-factory installer (Python venv + deps + browser core)
echo ============================================================
echo.

REM ---- 1. find Python (>=3.10) ----
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (
  where python >nul 2>nul && set PY=python
)
if "%PY%"=="" (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo         Check "Add Python to PATH" during install, then run this script again.
  pause
  exit /b 1
)
echo [1/5] Python: %PY%
%PY% --version

REM ---- 2. create venv ----
if exist ".venv\Scripts\python.exe" (
  echo [2/5] venv exists, skip create.
) else (
  echo [2/5] creating venv .venv ...
  %PY% -m venv .venv
  if errorlevel 1 ( echo [ERROR] venv create failed & pause & exit /b 1 )
)

set VENV_PY=.venv\Scripts\python.exe

REM ---- 3. install deps ----
echo [3/5] installing deps (pip install -r requirements.txt) ...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>nul
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] deps install failed, check network/pip mirror & pause & exit /b 1 )

REM ---- 4. install Playwright Chromium ----
echo [4/5] installing Playwright Chromium ...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 ( echo [WARN] playwright core install failed, run later: .venv\Scripts\playwright install chromium )

REM ---- 5. prepare .env ----
if exist ".env" (
  echo [5/5] .env exists, keep your config.
) else (
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo [5/5] .env created from template, fill keys later in the web panel Config page.
  ) else (
    echo [5/5] .env.example not found, skip.
  )
)

echo.
echo ============================================================
echo   Install done!
echo   - Start BitBrowser/AdsPower and Clash Verge clients
echo   - Double-click start.bat to open the control panel
echo ============================================================
pause
