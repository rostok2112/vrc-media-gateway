@echo off

REM %~dp0 всегда с \ в конце — убираем его
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%

REM запуск nginx с конфигом из этой же папки
nginx.exe -p "%ROOT%" -c main.conf

pause
