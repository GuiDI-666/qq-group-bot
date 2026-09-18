@echo off
chcp 65001 >nul
title QQ群管机器人 一键部署
echo 正在启动部署向导（如缺 VC++ 运行库会请求管理员权限）...
python "%~dp0deploy\deploy.py"
if errorlevel 1 (
  echo.
  echo 部署出错，请把上方报错信息发给管理员。
  pause
)
