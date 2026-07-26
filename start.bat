@echo off
rem AsterMem one-command launcher (Windows double-click entry)
rem Actual logic lives in start.ps1; this file only bypasses the default script
rem execution policy to launch it, and keeps the window open on failure so
rem users who double-clicked can see the error message.

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
    echo.
    echo [AsterMem] Startup failed, exit code %EXITCODE%
    pause
)
exit /b %EXITCODE%
