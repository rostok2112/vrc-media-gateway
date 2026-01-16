@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set INPUT_DIR=input
set HTML_DIR=html
set CACHE_DIR=output

if not exist "%INPUT_DIR%" (
    echo [ERROR] input folder not found
    pause
    exit /b
)

if not exist "%HTML_DIR%" mkdir "%HTML_DIR%"
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

echo Clearing html directory...
del /q "%HTML_DIR%\*.*" >nul 2>&1

for %%F in ("%INPUT_DIR%\*") do (

    set "INFILE=%%~fF"
    set "NAME=%%~nF"
    set "EXT=%%~xF"
    set "OUT_DIR=%CACHE_DIR%\%%~nF"

    echo.
    echo Processing: %%~nxF

    if not exist "!OUT_DIR!" mkdir "!OUT_DIR!"

    REM =========================
    REM Detect audio vs video
    REM =========================
    set MODE=VIDEO
    if /I "!EXT!"==".mp3" set MODE=AUDIO
    if /I "!EXT!"==".wav" set MODE=AUDIO
    if /I "!EXT!"==".flac" set MODE=AUDIO
    if /I "!EXT!"==".aac" set MODE=AUDIO
    if /I "!EXT!"==".ogg" set MODE=AUDIO

    REM =========================
    REM AUDIO → HLS (stereo!)
    REM =========================
    if "!MODE!"=="AUDIO" (
        echo Audio detected → converting to stereo HLS

        ffmpeg -y -i "!INFILE!" ^
          -vn ^
          -c:a aac ^
          -profile:a aac_low ^
          -ac 2 ^
          -ar 48000 ^
          -b:a 192k ^
          -f hls ^
          -hls_time 4 ^
          -hls_list_size 0 ^
          -hls_playlist_type vod ^
          -hls_flags independent_segments ^
          "!OUT_DIR!\index.m3u8"

    ) else (

    REM =========================
    REM VIDEO → HLS (FIXED AUDIO)
    REM =========================
        echo Video detected → converting with stereo downmix

        ffmpeg -y -i "!INFILE!" ^
          -map 0:v:0 -map 0:a:0 ^
          -c:v libx264 ^
          -preset fast ^
          -profile:v high ^
          -level 4.2 ^
          -pix_fmt yuv420p ^
          -g 60 ^
          -keyint_min 60 ^
          -sc_threshold 0 ^
          -c:a aac ^
          -profile:a aac_low ^
          -ac 2 ^
          -ar 48000 ^
          -b:a 192k ^
          -af "pan=stereo|FL<0.8*FL+0.6*FC+0.6*BL|FR<0.8*FR+0.6*FC+0.6*BR" ^
          -f hls ^
          -hls_time 4 ^
          -hls_list_size 0 ^
          -hls_playlist_type vod ^
          -hls_flags independent_segments ^
          "!OUT_DIR!\index.m3u8"
    )

    echo Updating active stream...
    del /q "%HTML_DIR%\*.*" >nul 2>&1
    xcopy "!OUT_DIR!\*" "%HTML_DIR%\" /E /I /Y >nul
)

echo.
echo DONE.
echo Active stream ready in html\
pause
