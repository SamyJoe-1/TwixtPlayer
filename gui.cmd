@echo off
rem The twixt manager window. Double-click, or drag a video onto this file.
setlocal
cd /d "%~dp0"
python -c "import PySide6" 2>nul || (
    echo Installing the GUI toolkit ^(PySide6^), one time only...
    python -m pip install --user PySide6
)
start "" pythonw -m twixt gui %1
