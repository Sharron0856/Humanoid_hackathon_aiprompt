@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ラジオ体操 demo 启动器(真机)
echo ==========================================
echo   ラジオ体操 demo 一键启动(真机 G1)
echo ==========================================
echo   前提:防倒架已挂好 / 急停人员就位 / probe 已通过
echo.
choice /c 12 /m "视觉摄像头: 1=机器人外接Brio  2=笔记本摄像头"
if errorlevel 2 (set "CAMARG=--cam 0") else (set "CAMARG=")

set VISION_NAMES=AB
start "vision_coach 视觉教练" cmd /k "set VISION_NAMES=AB&.venv-real\Scripts\python.exe vision_coach.py %CAMARG% --show"

set PYTHONUTF8=1
echo.
echo  接下来在本窗口按真机安全流程操作:
echo    1. 输入 ENABLE REAL ROBOT 确认
echo    2. 大家站好位后输入 p7(通し),再输入 RUN 确认码 = 正式开始
echo    3. 紧急情况:急停遥控器(软件里 Ctrl+C 只停发新指令)
echo  ④⑤两节用到腰,已确认硬件才在下面命令里加 --allow-waist-roll-pitch
echo.
.venv-real\Scripts\python.exe demo_robot.py --robot-dof 29 --interface 192.168.123.222 --execute --allow-waist-roll-pitch --robot-speaker
pause
