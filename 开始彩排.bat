@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ラジオ体操 demo 启动器(仿真彩排)
echo ==========================================
echo   ラジオ体操 demo 一键启动(仿真彩排)
echo ==========================================
echo.
choice /c 12 /m "视觉摄像头: 1=机器人外接Brio  2=笔记本摄像头"
if errorlevel 2 (set "CAMARG=--cam 0") else (set "CAMARG=")

set VISION_NAMES=AB
start "vision_coach 视觉教练" cmd /k "set VISION_NAMES=AB&.venv-real\Scripts\python.exe vision_coach.py %CAMARG% --show"

set PYTHONUTF8=1
set DEMO_PAUSED=1
echo.
echo  仿真窗口即将打开(启动即暂停待命)。
echo  流程:大家站好位 - 点一下仿真窗口 - 按【空格】开始演出
echo        随时按【空格】暂停/继续,按【R】从头重来
echo.
python demo_voice.py
pause
