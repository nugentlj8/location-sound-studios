@echo off
title Location Sound Studios
cd /d "%~dp0lss_studio"
py lss_studio.py
if errorlevel 1 (
  echo.
  echo  Something went wrong. The error is above.
  echo  If this is the first run, use Setup.bat first.
  echo.
  pause
)
