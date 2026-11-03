@echo off
rem Installs See-through (https://github.com/shitagaki-lab/see-through), the model
rem that splits one illustration into layers (hair, face, eyes, mouth, ...).
rem Only needed to MAKE an avatar rig; Amadeus itself never runs it.
rem
rem   tools\seethrough\install.bat [INSTALL_DIR]      default: \SeeThrough on this drive
rem
rem Needs Python 3.12 (py launcher), git, and an NVIDIA GPU with 12 GB+ VRAM.
rem Downloads about 17.5 GB: PyTorch for CUDA 12.8 (~4 GB) and the weights (~13.4 GB).
setlocal
if not "%~1"=="" set "ST_HOME=%~f1"
call "%~dp0env.bat"
echo Installing See-through into %ST_HOME%
if not exist "%ST_HOME%" mkdir "%ST_HOME%"

where git >nul 2>nul || (echo git is required: https://git-scm.com/download/win & exit /b 1)
py -3.12 -c "pass" >nul 2>nul || (echo Python 3.12 is required: https://www.python.org/downloads/ & exit /b 1)

if not exist "%ST_REPO%\.git" (
  git clone --depth 1 https://github.com/shitagaki-lab/see-through "%ST_REPO%" || exit /b 1
)
if not exist "%ST_PY%" (
  py -3.12 -m venv "%ST_HOME%\venv" || exit /b 1
)
"%ST_PY%" -m pip install --upgrade pip || exit /b 1
"%ST_PY%" -m pip install torch==2.8.0+cu128 torchvision==0.23.0+cu128 --index-url https://download.pytorch.org/whl/cu128 || exit /b 1

pushd "%ST_REPO%"
"%ST_PY%" -m pip install -r requirements.txt || (popd & exit /b 1)
rem The README's "ln -sf common/assets assets"; Windows gets a copy.
if not exist assets xcopy /e /i /q common\assets assets >nul
popd

rem Weights go to %HF_HOME%, under the repo ids inference_psd.py asks for.
"%ST_HOME%\venv\Scripts\hf.exe" download layerdifforg/seethroughv0.0.2_layerdiff3d || exit /b 1
"%ST_HOME%\venv\Scripts\hf.exe" download 24yearsold/seethroughv0.0.1_marigold || exit /b 1

echo.
echo See-through is installed. Make a rig with:  tools\make_rig.bat IMAGE
