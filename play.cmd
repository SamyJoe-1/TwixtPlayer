@echo off
rem Play a video with frame generation. Drag a file onto this, or pass a path.
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: play.cmd "C:\path\to\movie.mkv"
    echo    or: drag a video file onto this file.
    pause
    exit /b 1
)

rem 72 fps ^(3x from 24p^) is the rate measured to hold on this machine
rem without dropping frames. Raise --cap if you upgrade the CPU.
python -m twixt play %1 --profile fast --cap 72
