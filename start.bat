@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo First run: setting up. This takes a minute or two and needs the internet once.
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 or newer is needed.
    exit /b 1
  )
)

if not exist .venv\.installed (
  .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  .venv\Scripts\python.exe -m pip install --quiet -e .
  if errorlevel 1 exit /b 1
  type nul > .venv\.installed
)

.venv\Scripts\python.exe -m larder %*
