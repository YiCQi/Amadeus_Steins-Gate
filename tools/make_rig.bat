@echo off
rem Build the avatar rig from one portrait:
rem   1. See-through splits it into layers  -> <name>.psd (+ _depth.psd) next to the image
rem   2. psd_to_rig.py meshes the layers     -> web\assets\kurisu\rig\
rem
rem   tools\make_rig.bat IMAGE              e.g. tools\make_rig.bat art\kurisu\kurisu_clean.png
rem   tools\make_rig.bat IMAGE PSD          skip step 1 and use an existing PSD
rem                                         (e.g. one downloaded from the online demo)
rem
rem Screenshots with CRT scanlines: run tools\clean_scanlines.py on them first.
rem Step 1 needs tools\seethrough\install.bat; step 2 alone does not.
setlocal
call "%~dp0seethrough\env.bat"
set "ROOT=%~dp0.."
set "SRC=%~f1"
set "NAME=%~n1"
if "%SRC%"=="" (echo usage: tools\make_rig.bat IMAGE [PSD] & exit /b 1)
if not exist "%SRC%" (echo not found: %SRC% & exit /b 1)
set "ART=%~dp1"
rem psd_to_rig.py runs in See-through's venv, or else in the app's own (start.bat installs psd-tools)
set "PY=%ST_PY%"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Amadeus\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if not "%~2"=="" (
  set "PSD=%~f2"
  goto convert
)
if not exist "%ST_PY%" (echo See-through is not installed: run tools\seethrough\install.bat & exit /b 1)
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "WORK=%ST_HOME%\work"
pushd "%ST_REPO%"
"%ST_PY%" inference\scripts\inference_psd.py --srcp "%SRC%" --save_dir "%WORK%" --save_to_psd --tblr_split
if errorlevel 1 (
  echo Out of GPU memory? Retrying with --group_offload ^(slower, about 10 GB^)...
  "%ST_PY%" inference\scripts\inference_psd.py --srcp "%SRC%" --save_dir "%WORK%" --save_to_psd --tblr_split --group_offload || (popd & exit /b 1)
)
popd
copy /y "%WORK%\%NAME%.psd" "%ART%" >nul || exit /b 1
copy /y "%WORK%\%NAME%_depth.psd" "%ART%" >nul
copy /y "%WORK%\%NAME%.psd.json" "%ART%" >nul
set "PSD=%ART%%NAME%.psd"

:convert
"%PY%" "%ROOT%\tools\psd_to_rig.py" "%PSD%" --source "%SRC%" --out "%ROOT%\web\assets\kurisu\rig" || exit /b 1
echo.
echo Rig built. Reload the page (Ctrl+F5). To check the layers: web\assets\kurisu\rig\preview.png
