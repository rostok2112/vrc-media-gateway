@echo off
echo Updating yt-dlp...
yt-dlp -U

if %errorlevel% == 0 (
    echo Update successful
) else (
    echo Update failed
)

pause