@echo off
rem ===================================================================
rem  QQ Bot - server / new-machine deployment helper
rem
rem  WHY THIS FILE IS PURE ASCII:
rem    A UTF-8 .bat containing Chinese text, combined with "chcp 65001",
rem    makes cmd.exe lose track of its byte offset while re-reading the
rem    file. Commands get shredded and the window closes instantly.
rem    Keeping this file ASCII-only removes that whole failure mode.
rem
rem  WHAT IT DOES
rem    1. finds a usable Python (3.9 - 3.13), installs 3.13 if there is none
rem    2. installs the dependencies (aliyun mirror -> pypi.org fallback)
rem    3. adapts the deployment config to this machine (NapCat dir, interpreter)
rem    4. runs deploy.py, then fixes .bat encodings for this machine
rem
rem  Everything is logged to server-deploy-log.txt next to this file.
rem ===================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 936 >nul 2>nul
title QQ Bot - Server Deploy

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "LOG=%ROOT%\server-deploy-log.txt"
set "PREP=%ROOT%\deploy\server_prepare.py"
set "REQ=%ROOT%\deps\requirements-full.txt"
set "IDX=https://mirrors.aliyun.com/pypi/simple/"
set "IDXHOST=mirrors.aliyun.com"
set "PYVER=3.13.9"
set "PYINST=%TEMP%\python-%PYVER%-amd64.exe"
set "PYEXE="
set "PYSHOW="
set "DL_OK="

>"%LOG%" echo ============================================================
>>"%LOG%" echo  QQ Bot - server deployment
>>"%LOG%" echo  start : %DATE% %TIME%
>>"%LOG%" echo  root  : %ROOT%
>>"%LOG%" echo ============================================================

echo.
echo  ==========================================================
echo   QQ Bot - server / new-machine deployment
echo  ==========================================================
echo   project : %ROOT%
echo   log     : server-deploy-log.txt
echo  ==========================================================
echo.

if not exist "%ROOT%\deploy\deploy.py" (
  call :SAY "[FAIL] deploy\deploy.py not found."
  call :SAY "       Put this file inside the project root folder, then run again."
  goto :END
)
if not exist "%PREP%" (
  call :SAY "[FAIL] deploy\server_prepare.py not found."
  call :SAY "       Copy the whole deploy folder from the source machine."
  goto :END
)

net session >nul 2>nul
if errorlevel 1 call :SAY "note: not elevated - if the VC++ runtime is missing, the installer may ask for permission."

call :SAY "[1/6] looking for Python 3.9 - 3.13 ..."
call :FIND_PY
if not defined PYEXE call :INSTALL_PY
call :FIND_PY
if not defined PYEXE (
  call :SAY "[FAIL] no usable Python, and the automatic install did not work."
  call :SAY "       Install Python 3.13 manually from https://www.python.org"
  call :SAY "       tick [Add python.exe to PATH], then run this file again."
  goto :END
)
call :SAY "      using: !PYSHOW!"
>>"%LOG%" echo [ok] selected interpreter: !PYSHOW!

call :SAY "[2/6] upgrading pip ..."
!PYEXE! -m pip install --upgrade pip -i %IDX% --trusted-host %IDXHOST% >>"%LOG%" 2>&1
call :SAY "      pip exit code: !ERRORLEVEL!"

call :SAY "[3/6] installing dependencies, this can take a few minutes ..."
!PYEXE! -m pip install -r "%REQ%" -i %IDX% --trusted-host %IDXHOST% >>"%LOG%" 2>&1
if errorlevel 1 (
  call :SAY "      aliyun mirror failed, retrying with pypi.org ..."
  !PYEXE! -m pip install -r "%REQ%" -i https://pypi.org/simple >>"%LOG%" 2>&1
)
!PYEXE! -c "import nonebot,nonebot.adapters.onebot.v11" >>"%LOG%" 2>&1
if errorlevel 1 (
  call :SAY "[FAIL] nonebot is still not importable. See server-deploy-log.txt"
  goto :END
)
call :SAY "      dependencies OK."

call :SAY "[4/6] adapting the project to this machine ..."
!PYEXE! "%PREP%" "%ROOT%" >>"%LOG%" 2>&1
if errorlevel 1 call :SAY "      [WARN] prepare step exit code !ERRORLEVEL! - see deploy-report.txt"

call :SAY "[5/6] running the official deploy script ..."
!PYEXE! "%ROOT%\deploy\deploy.py"
if errorlevel 1 call :SAY "      [WARN] deploy.py exit code !ERRORLEVEL!"

call :SAY "[6/6] fixing .bat encodings for this machine ..."
!PYEXE! "%PREP%" --encoding-only "%ROOT%" >>"%LOG%" 2>&1
if errorlevel 1 call :SAY "      [WARN] encoding fix exit code !ERRORLEVEL!"

:END
echo.
echo  ==========================================================
echo   Finished.
echo   log    : %ROOT%\server-deploy-log.txt
echo   report : %ROOT%\deploy-report.txt
echo   next   : double-click  yi-jian-bu-shu .bat  (deploy)
echo            then             qi-dong-ji-qi-ren .bat (run)
echo  ==========================================================
echo.
pause
exit /b 0


rem ===================================================================
rem  subroutines
rem ===================================================================

:SAY
echo %~1
>>"%LOG%" echo %~1
exit /b 0


rem -- try candidate interpreters in order, keep the first working 3.9 ~ 3.13 --
:FIND_PY
set "PYEXE="
set "PYSHOW="
call :TRY "%ROOT%\venv\Scripts\python.exe"
call :TRY "%ROOT%\.venv\Scripts\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :TRY "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :TRY "C:\Python313\python.exe"
call :TRY "C:\Python312\python.exe"
call :TRY "C:\Python311\python.exe"
call :TRY "C:\Program Files\Python313\python.exe"
call :TRY "C:\Program Files\Python312\python.exe"
call :TRY "C:\Program Files\Python311\python.exe"
rem skip the WindowsApps store alias - it opens the Store instead of running python
for /f "delims=" %%P in ('where python 2^>nul ^| findstr /i /v "WindowsApps"') do call :TRY "%%P"
for /f "delims=" %%P in ('where python3 2^>nul ^| findstr /i /v "WindowsApps"') do call :TRY "%%P"
exit /b 0


:TRY
if defined PYEXE exit /b 0
if "%~1"=="" exit /b 0
rem only real .exe: a python.cmd/.bat has two nasty batch quirks
rem (redirect leak, and control never returning to this script)
if /i not "%~x1"==".exe" exit /b 0
if not exist "%~1" exit /b 0
rem Do NOT redirect here. When the candidate interpreter is itself a .cmd/.bat,
rem a redirect on this line is never restored and silently swallows every later
rem echo of the parent script (the window looks frozen while it keeps running).
rem Instead rely on the exit code: python prints nothing for SystemExit(int).
rem "call" so that a candidate which is a .cmd/.bat wrapper also returns properly.
call "%~1" -c "import sys;raise SystemExit(0 if sys.version_info[0]*100+sys.version_info[1] in [309,310,311,312,313] else 1)"
if errorlevel 1 exit /b 0
set PYEXE="%~1"
set "PYSHOW=%~1"
exit /b 0


rem -- no suitable Python: install official 3.13 silently (per-user, no admin) --
:INSTALL_PY
call :SAY "      no suitable Python found - downloading Python %PYVER% ..."
call :DOWNLOAD "https://mirrors.aliyun.com/python-release/windows/python-%PYVER%-amd64.exe"
if not defined DL_OK call :DOWNLOAD "https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-amd64.exe"
if not defined DL_OK call :DOWNLOAD "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
if not defined DL_OK (
  call :SAY "[FAIL] could not download the Python installer (no route to the mirrors?)."
  exit /b 0
)
call :SAY "      running the silent installer, please wait ..."
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0 Include_tcltk=0
call :SAY "      installer exit code: !ERRORLEVEL!"
rem the bootstrapper can return before the install really finishes; poll for a while
call :FIND_PY
set "WAITN=0"

:PY_WAIT
if defined PYEXE exit /b 0
if !WAITN! GEQ 15 exit /b 0
set /a WAITN+=1
call :SAY "      waiting for the installer, attempt !WAITN! of 15 ..."
ping -n 11 127.0.0.1 >nul
call :FIND_PY
goto :PY_WAIT


rem -- download: curl -> certutil -> PowerShell, first success sets DL_OK --
:DOWNLOAD
set "DL_OK="
if exist "%PYINST%" del /q "%PYINST%" >nul 2>nul
call :SAY "      downloading ..."
where curl >nul 2>nul
if not errorlevel 1 curl -L --fail --connect-timeout 20 --max-time 1200 -o "%PYINST%" "%~1" >>"%LOG%" 2>&1
if exist "%PYINST%" for %%F in ("%PYINST%") do if %%~zF GTR 1048576 set "DL_OK=1"
if defined DL_OK exit /b 0
call :SAY "      curl did not work, trying certutil ..."
certutil -urlcache -split -f "%~1" "%PYINST%" >>"%LOG%" 2>&1
if exist "%PYINST%" for %%F in ("%PYINST%") do if %%~zF GTR 1048576 set "DL_OK=1"
if defined DL_OK exit /b 0
call :SAY "      certutil did not work, trying PowerShell ..."
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%~1' -OutFile '%PYINST%' -UseBasicParsing" >>"%LOG%" 2>&1
if exist "%PYINST%" for %%F in ("%PYINST%") do if %%~zF GTR 1048576 set "DL_OK=1"
exit /b 0
