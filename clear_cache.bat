@echo off

echo Deleting contents of .\output\
del /f /s /q ".\output\*"
for /d %%p in (".\output\*") do rmdir /s /q "%%p"

echo Deleting contents of .\html\streams\
del /f /s /q ".\html\streams\*"
for /d %%p in (".\html\streams\*") do rmdir /s /q "%%p"

echo Done
pause