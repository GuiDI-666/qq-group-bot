@echo off
chcp 65001 >nul
title QQ群管机器人 关闭开机自启
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python "%~dp0deploy\autostart.py" uninstall
echo.
pause >nul
