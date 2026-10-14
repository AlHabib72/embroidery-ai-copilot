@echo off
REM Double-click to start the Embroidery AI Copilot dashboard (needs Python 3.10+ and: pip install -e .)
cd /d "%~dp0"
python -m embcopilot web
pause
