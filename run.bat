@echo off
rem Starts XiControl. The first run creates a private Python environment and installs packages;
rem later runs update the packages whenever requirements.txt has changed.
cd /d "%~dp0"

rem A .venv whose Python was removed or damaged is rebuilt from scratch.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul || (
        echo The private Python environment is damaged - rebuilding it...
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    set "PY="
    py -3 --version >nul 2>nul && set "PY=py -3"
    if not defined PY python --version >nul 2>nul && set "PY=python"
    if not defined PY goto nopython
    echo First run: setting up XiControl, this takes a minute or two...
    call %%PY%% -m venv .venv || goto nopython
)

fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul || (
    echo Installing the required packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
    copy /y requirements.txt ".venv\requirements.installed" >nul
)
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:nopython
echo.
echo Python 3.10 or newer is required, but no working Python was found.
echo  - Not installed: download it from https://www.python.org/downloads/
echo    and tick "Add python.exe to PATH" during setup.
echo  - Installed but broken: Windows Settings - Apps - Python - Modify - Repair.
pause
exit /b 1

:failed
echo.
echo Installing the required packages failed - check the internet connection and run this again.
pause
exit /b 1
