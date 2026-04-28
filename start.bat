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

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Please install Python 3.10+.
    pause
    exit /b 1
)

:: Create venv if not exists
if not exist .venv\Scripts\activate.bat (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

:: Activate venv
call .venv\Scripts\activate.bat

:: Install dependencies
echo [INFO] Installing dependencies...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

:: Create data directory
if not exist data mkdir data
if not exist uploads mkdir uploads

:: Start server
echo.
echo [OK] Starting server on http://localhost:8888
echo Press Ctrl+C to stop.
echo.

python run.py
