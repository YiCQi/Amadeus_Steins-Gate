@echo off
rem Installs the "mio/amadeus" voice: an ESPnet VITS model of Kurisu's Japanese voice
rem (https://huggingface.co/mio/amadeus, CC-BY-4.0). It runs on the CPU, so no GPU is needed.
rem
rem   tools\voice\install_amadeus_vits.bat
rem
rem Needs Python 3.12 (py launcher). Downloads about 0.8 GB: PyTorch for CPU, ESPnet, the
rem OpenJTalk reader, and the 373 MB model. Goes into %LOCALAPPDATA%\Amadeus (outside this
rem project: the ";" in its folder name breaks venvs).
setlocal
set "VENV=%LOCALAPPDATA%\Amadeus\vits-venv"
set "HF_HOME=%LOCALAPPDATA%\Amadeus\hf"
rem a global HF_ENDPOINT pointing at hf-mirror.com only redirects, which huggingface_hub rejects
set "HF_ENDPOINT=https://huggingface.co"

py -3.12 -c "pass" >nul 2>nul || (echo Python 3.12 is required: https://www.python.org/downloads/ & exit /b 1)
if not exist "%VENV%\Scripts\python.exe" (
  py -3.12 -m venv "%VENV%" || exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install "torch>=2.11,<2.12" "torchaudio>=2.11,<2.12" --index-url https://download.pytorch.org/whl/cpu || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install espnet espnet_model_zoo pyopenjtalk-plus jaconv || exit /b 1
"%VENV%\Scripts\hf.exe" download mio/amadeus || exit /b 1
echo.
echo The Amadeus voice is installed.
