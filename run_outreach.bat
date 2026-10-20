@echo off
REM ===========================================================================
REM  EmbroideryCopilot Outreach CRM -- launcher
REM  Usage:  run_outreach.bat [discover|preview|dryrun|send|gui|bot]
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

REM --- Locate the interpreter ---------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] No Python interpreter found on PATH.
  echo         Install Python 3.14 from https://python.org and tick "Add python.exe to PATH".
  exit /b 1
)

echo Using interpreter: %PY%
%PY% -c "import sys; print('Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
  echo [ERROR] The Python on PATH is not usable.
  exit /b 1
)

if "%ZOHO_SMTP_PASS%"=="" (
  echo [INFO] ZOHO_SMTP_PASS is not set.
  echo        Copy .env.example to .env and fill it in, or set the variable:
  echo            set ZOHO_SMTP_PASS=your-zoho-app-password
  echo        Sending will be refused until it is set.
  echo.
)

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=gui"

if /i "%ACTION%"=="discover" (
  echo Running lead_finder.py ...
  %PY% lead_finder.py
  goto :end
)
if /i "%ACTION%"=="preview" (
  echo Running email_agent_zoho.py --preview ...
  %PY% email_agent_zoho.py --preview
  goto :end
)
if /i "%ACTION%"=="dryrun" (
  echo Running email_agent_zoho.py --dry-run ...
  %PY% email_agent_zoho.py --dry-run
  goto :end
)
if /i "%ACTION%"=="send" (
  echo Running email_agent_zoho.py --send --confirm ...
  %PY% email_agent_zoho.py --send --confirm
  goto :end
)
if /i "%ACTION%"=="bot" (
  echo Running telegram_bot.py ...
  %PY% telegram_bot.py
  goto :end
)
if /i "%ACTION%"=="gui" (
  echo Starting the Outreach CRM dashboard ...
  %PY% app_dashboard.py
  goto :end
)
if /i "%ACTION%"=="test" (
  echo Running the outreach test suite ...
  %PY% -m pytest tests\test_outreach.py -q
  goto :end
)

echo [ERROR] Unknown action "%ACTION%".
echo Usage: run_outreach.bat [discover^|preview^|dryrun^|send^|gui^|bot^|test]
exit /b 2

:end
echo.
echo --- DONE ---
endlocal
