@echo off
chcp 65001 >nul 2>&1
title Stock Analysis Hub

echo ========================================
echo   Stock Analysis Hub - One-Click Start
echo ========================================
echo.

cd /d "%~dp0"

:: Check .env
if not exist .env (
    echo [ERROR] .env file not found!
    echo Please copy .env.example to .env and fill in your Baidu OCR credentials.
    echo.
    echo   copy .env.example .env
    echo.
    pause
    exit /b 1
)

:: Check Python: prefer venv, then py launcher, then system python
set "PY="
if exist .venv\Scripts\python.exe (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY=py"
    ) else (
        where python >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY=python"
        )
    )
)
if not defined PY (
    echo [ERROR] Python not found! Please install Python 3.10+.
    pause
    exit /b 1
)

:: Create venv if not exists
if not exist .venv\Scripts\python.exe (
    echo [INFO] Creating virtual environment...
    %PY% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set "PY=.venv\Scripts\python.exe"
    echo [OK] Virtual environment created.
)

:: Activate venv (only if not already using venv python)
if not exist .venv\Scripts\activate.bat (
    echo [ERROR] Virtual environment is broken. Delete .venv folder and retry.
    pause
    exit /b 1
)

:: Install dependencies (skip if pip unavailable - deps may already be installed)
echo [INFO] Checking Python dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt -q 2>nul
echo [OK] Python dependencies ready.

:: Build frontend if not built
if not exist frontend\dist\index.html (
    echo [INFO] Building frontend...
    where npm >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Node.js/npm not found! Install Node.js to build the frontend.
        echo Or manually run: cd frontend ^&^& npm install ^&^& npm run build
        pause
        exit /b 1
    )
    cd frontend
    call npm install
    call npx vite build
    cd ..
    if not exist frontend\dist\index.html (
        echo [ERROR] Frontend build failed.
        pause
        exit /b 1
    )
    echo [OK] Frontend built.
)

:: Create data directories
if not exist data mkdir data
if not exist uploads mkdir uploads

:: Start server and open browser
echo.
echo [OK] Starting server on http://localhost:8888
echo Press Ctrl+C to stop.
echo.

start http://localhost:8888/admin
"%PY%" run.py
