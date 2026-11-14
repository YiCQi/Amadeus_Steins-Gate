@echo off
rem Amadeus launcher. Creates .venv beside this file on the first run and keeps it current.
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"

rem Python refuses to build a virtual environment under a path containing ';', since that is
rem what Windows separates PATH with. Say so plainly rather than failing further down.
echo "%~dp0" | find ";" >nul
if not errorlevel 1 (
    echo [Amadeus] This folder's path contains a semicolon:
    echo [Amadeus]   %~dp0
    echo [Amadeus] Python will not create a virtual environment there. Rename the folder
    echo [Amadeus] ^(Amadeus_Steins-Gate, say^) and run this again.
    pause
    exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [Amadeus] Creating Python environment in .venv ...
    python -m venv "%VENV%" || goto :fail
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
)
rem Quick when everything is already installed; picks up new dependencies after updates.
"%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail

if not exist config.toml copy config.example.toml config.toml >nul

"%VENV%\Scripts\python.exe" run.py
goto :eof

:fail
echo [Amadeus] Setup failed.
pause
