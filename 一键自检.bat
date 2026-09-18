@echo off
chcp 65001 >nul
title QQ群管机器人 部署自检
python "%~dp0deploy\self_check.py"
echo.
pause
