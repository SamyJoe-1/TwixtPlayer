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

rem twixt-1440p.json is measured, not guessed: 138 fps against its 90 fps
rem target on this machine (1.53x). 90 is exactly half of the 180 Hz panel, so
rem every frame occupies precisely two refreshes and nothing gets resampled.
rem
rem 120 fps also holds (1.33x) but 180/120 is 1.5 refreshes per frame, which
rem alternates 1-2-1-2 and beats visibly. Use twixt-1440p-120hz.json ONLY with
rem the monitor set to 120 Hz. 180 fps out was measured at 0.83x -- not possible.
rem
rem The GPU is deliberately not doing the interpolation. RIFE measured 23 fps at
rem 2560x1440 on this card against mvtools' 138; it only wins at 1080p or below.
rem The GPU still decodes the AV1 (--hwdec=auto-copy) and drives the output.
rem Re-measure on a different file with:  twixt tune "<file>" --with-rife
python -m twixt play %1 --profile twixt-1440p.json
