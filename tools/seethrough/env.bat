@echo off
rem Shared paths for the See-through scripts. Set ST_HOME first to use another folder.
rem Default: \SeeThrough on this drive (outside this project: the ";" in its name breaks venvs).
if not defined ST_HOME set "ST_HOME=%~d0\SeeThrough"
set "ST_REPO=%ST_HOME%\see-through"
set "ST_PY=%ST_HOME%\venv\Scripts\python.exe"
set "HF_HOME=%ST_HOME%\hf"
rem A global HF_ENDPOINT=https://hf-mirror.com breaks downloads: the mirror now answers
rem with a redirect to huggingface.co, which huggingface_hub rejects. Set ST_HF_ENDPOINT
rem to use a mirror that really serves files.
if not defined ST_HF_ENDPOINT set "ST_HF_ENDPOINT=https://huggingface.co"
set "HF_ENDPOINT=%ST_HF_ENDPOINT%"
