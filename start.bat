@echo off
rem Amadeus launcher. The venv lives outside this folder because the ';' in
rem "Amadeus_Steins;Gate" breaks Python virtual environments.
setlocal
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\Amadeus\venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo [Amadeus] Creating Python environment in %VENV% ...
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
