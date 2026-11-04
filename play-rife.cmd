@echo off
rem Play with RIFE on the GPU (TensorRT) instead of mvtools on the CPU.
rem Much less ghosting / block tearing on fast motion. The first run on a new
rem frame size compiles a TensorRT engine -- that takes minutes, once.
rem Check it holds first:  twixt tune "<file>" --backend rife
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: play-rife.cmd "C:\path\to\movie.mkv"
    echo    or: drag a video file onto this file.
    pause
    exit /b 1
)

python -m twixt play %1 --profile twixt-1440p-rife.json
