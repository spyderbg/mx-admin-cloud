@echo off
setlocal

REM Windows CMD startup script for backend API
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
) else (
  python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
)