@echo off
rem Run twixt from anywhere -- resolve to this script's directory first.
setlocal
cd /d "%~dp0"
python -m twixt %*
