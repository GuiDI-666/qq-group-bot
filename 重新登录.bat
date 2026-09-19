@echo off
chcp 936 >nul
title QQ群管机器人 重新登录
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   重新登录协议端（NapCat）
echo   会重启 QQ 协议端并尝试免扫码快速登录
echo ==========================================
echo.
echo 如果免扫码失败，会自动生成二维码：
echo   %~dp0需要扫码登录.png
echo （用手机 QQ 扫描该图片，选择机器人小号授权）
echo.
set "PY="
call :TRY "%~dp0venv\Scripts\python.exe"
call :TRY "%~dp0.venv\Scripts\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :TRY "C:\Python313\python.exe"
call :TRY "C:\Python312\python.exe"
call :TRY "C:\Python311\python.exe"
call :TRY "C:\Program Files\Python313\python.exe"
call :TRY "C:\Program Files\Python312\python.exe"
call :TRY "C:\Program Files\Python311\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul ^| findstr /i /v "WindowsApps"') do call :TRY "%%P"
)
if not defined PY (
  echo [错误] 没找到 Python 3.9 ~ 3.13。
  echo.
  echo   两个办法，任选其一：
  echo     1. 双击『服务器部署.bat』，它会自动下载安装合适的 Python
  echo     2. 自己装一个 Python 3.13，安装时勾选 "Add python.exe to PATH"
  echo.
  pause
  exit /b 1
)
echo 使用 Python：!PY!
echo.

"!PY!" "%~dp0deploy\watchdog.py" --relogin
echo.
echo 完成。如仍需扫码，请看上面的图片路径。
pause
exit /b 0

:TRY
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys;v=sys.version_info;raise SystemExit(0 if v[0]==3 and v[1] in range(9,14) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
