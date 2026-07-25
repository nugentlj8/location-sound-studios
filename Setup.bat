@echo off
title Location Sound Studios - Setup
cd /d "%~dp0lss_studio"
echo.
echo  Location Sound Studios - one-time setup
echo  =======================================
echo.
py --version >nul 2>&1
if errorlevel 1 (
  echo  [X] Python not found. Install from https://www.python.org/downloads/
  echo      Tick "Add Python to PATH" on the first installer screen, then rerun.
  pause & exit /b 1
)
for /f "delims=" %%v in ('py --version') do echo  [ok] %%v
ffmpeg -version >nul 2>&1
if errorlevel 1 (
  echo  [..] installing ffmpeg
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  echo  ffmpeg installed. CLOSE this window and run Setup.bat again.
  pause & exit /b 0
)
echo  [ok] ffmpeg
echo  [..] installing Python packages
py -m pip install --quiet --upgrade pip numpy pillow
echo  [ok] numpy, pillow
echo.
echo  Setup complete. Launch with "Location Sound Studios.bat".
pause
