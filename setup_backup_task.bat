@echo off
chcp 65001 >nul 2>&1
title 设置每日备份定时任务

cd /d "%~dp0"

:: 确定 Python 路径
set "PY="
if exist .venv\Scripts\python.exe (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY=py"
    ) else (
        where python >nul 2>&1
        if %errorlevel% equ 0 (
            set "PY=python"
        )
    )
)
if not defined PY (
    echo [ERROR] 未找到 Python
    pause
    exit /b 1
)

set "SCRIPT=%~dp0backup.py"

:: 删除已有同名任务（避免重复）
schtasks /Delete /TN "StockAnalysisBackup" /F >nul 2>&1

:: 创建每天 20:00 执行的定时任务
schtasks /Create /TN "StockAnalysisBackup" /TR "\"%PY%\" \"%SCRIPT%\"" /SC DAILY /ST 20:00 /F
if %errorlevel% equ 0 (
    echo.
    echo [OK] 定时任务已创建：每天 20:00 自动备份数据库
    echo     任务名: StockAnalysisBackup
    echo.
    echo 管理命令:
    echo   查看任务: schtasks /Query /TN "StockAnalysisBackup"
    echo   手动运行: schtasks /Run /TN "StockAnalysisBackup"
    echo   删除任务: schtasks /Delete /TN "StockAnalysisBackup" /F
) else (
    echo [ERROR] 创建定时任务失败，请以管理员身份运行此脚本
)

echo.
pause
