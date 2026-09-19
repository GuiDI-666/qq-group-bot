@echo off
chcp 936 >nul
title QQ群管机器人 一键启动（看门狗模式）
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   QQ群管机器人 一键启动
echo   看门狗守护：协议端(NapCat) + 机器人(NoneBot)
echo   登录失效会自动重新登录，进程掉线会自动拉起
echo ==========================================
echo.
echo 机器人账号 / 端口 / 管理群：见 部署配置.json 与 qq-group-bot\config.json
echo 关闭本窗口即停止守护（协议端与机器人会一并退出）
echo.

rem ---------- 先挑一个"装了 nonebot"的 Python ----------
rem 电脑上可能装了多个 Python，只有装了 nonebot 的那个才能跑机器人服务。
rem 这里逐个试，挑第一个能 import nonebot 的，避免出现
rem "看门狗起来了、但机器人服务因缺少 nonebot 反复秒退"的情况。
set "PY="
if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul ^| findstr /i /v "WindowsApps"') do (
    if not defined PY (
      "%%P" -c "import nonebot" >nul 2>nul
      if !errorlevel! equ 0 set "PY=%%P"
    )
  )
)

rem 最后兜底：WorkBuddy 内置 Python（本机 nonebot 目前装在这里；别的机器上一般用不到）
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if not defined PY if exist "%%D\python.exe" (
    "%%D\python.exe" -c "import nonebot" >nul 2>nul
    if !errorlevel! equ 0 set "PY=%%D\python.exe"
  )
)

if not defined PY (
  echo [错误] 没有找到"已安装 nonebot"的 Python 解释器。
  echo.
  echo   三种解决办法（任选其一）：
  echo     1. 双击『一键部署.bat』，让脚本自动安装依赖
  echo     2. 把装有 nonebot 的 python 目录加到系统 PATH 的最前面
  echo     3. 在 部署配置.json 里写死解释器路径，例如：
  echo          "python_path": "C:\\Python313\\python.exe"
  echo.
  pause
  exit /b 1
)

echo 使用 Python：!PY!
echo.
"!PY!" "%~dp0deploy\watchdog.py"

echo.
echo 看门狗已退出。按任意键关闭窗口。
pause >nul
