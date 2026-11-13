@echo off
rem Builds XiControl so it runs without Python installed.
rem   build_exe.bat          -> dist\XiControl\XiControl.exe (+ dist\XiControl.zip to hand out)
rem   build_exe.bat onefile  -> dist\XiControl.exe (one big file; starts slower, antivirus likes it less)
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul || rmdir /s /q .venv
)
if not exist ".venv\Scripts\python.exe" (
    set "PY="
    py -3 --version >nul 2>nul && set "PY=py -3"
    if not defined PY python --version >nul 2>nul && set "PY=python"
    if not defined PY goto nopython
    call %%PY%% -m venv .venv || goto nopython
)
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul || (
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
    copy /y requirements.txt ".venv\requirements.installed" >nul
)
".venv\Scripts\python.exe" -m pip install pyinstaller || goto failed

rem the app icon is drawn by gui\icon.py; PyInstaller needs it as an .ico file
set "ICON=%TEMP%\xicontrol.ico"
".venv\Scripts\python.exe" -c "import sys; from gui.icon import save_ico; save_ico(sys.argv[1])" "%ICON%" || goto failed

set "MODE=--onedir"
if /i "%~1"=="onefile" set "MODE=--onefile"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed %MODE% --name XiControl ^
    --icon "%ICON%" ^
    --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick3D --exclude-module PySide6.QtPdf ^
    main.py || goto failed

if /i "%~1"=="onefile" (
    echo.
    echo Done: dist\XiControl.exe
    pause
    exit /b 0
)

rem Leave out the parts of Qt that XiControl never uses (about 40 MB):
rem   opengl32sw.dll - software OpenGL; the app draws with plain widgets and Direct3D video
rem   Qt Quick / QML - only pulled in by the on-screen (virtual) keyboard plugin
rem   translations   - Qt's own translations, which the app doesn't load
set "QT=dist\XiControl\_internal\PySide6"
del /q "%QT%\opengl32sw.dll" "%QT%\Qt6Quick*.dll" "%QT%\Qt6Qml*.dll" "%QT%\Qt6VirtualKeyboard*.dll" "%QT%\Qt6OpenGL*.dll" 2>nul
rmdir /s /q "%QT%\plugins\platforminputcontexts" "%QT%\translations" 2>nul

rem the built program checks itself (widgets, video, images, encryption, HTTPS) before it is zipped
start "" /wait "dist\XiControl\XiControl.exe" --selftest
if errorlevel 1 (
    echo.
    echo The self-test of the built program failed - see %APPDATA%\XiControl\xicontrol.log
    goto failed
)
powershell -NoProfile -Command "Compress-Archive -Path 'dist\XiControl' -DestinationPath 'dist\XiControl.zip' -Force"
echo.
echo Done: dist\XiControl\XiControl.exe  ^(and dist\XiControl.zip to copy to another PC^)
pause
exit /b 0

:nopython
echo Python 3.10 or newer is required: https://www.python.org/downloads/
echo If it is installed but broken: Windows Settings - Apps - Python - Modify - Repair.
pause
exit /b 1

:failed
echo Build failed - see the messages above.
pause
exit /b 1
