@echo off
setlocal
set "HERE=%~dp0"
set "PYW=%HERE%python\pythonw.exe"
set "PYX=%HERE%python\python.exe"

if exist "%PYW%" goto run
if exist "%PYX%" goto run

echo.
echo ============================================================
echo   TagMaster cannot start.
echo.
echo   Missing folder: %HERE%python
echo.
echo   Put the [python] folder (about 5.3 GB) next to TagMaster.py,
echo   then run this file again.
echo.
echo   Details: see README.md  -  chapter 4, item 1
echo ============================================================
echo.
if exist "%HERE%README.md" start "" notepad "%HERE%README.md"
pause
exit /b 1

:run
if exist "%PYW%" (
    start "" "%PYW%" "%HERE%TagMaster.py" %*
) else (
    start "" "%PYX%" "%HERE%TagMaster.py" %*
)
exit /b 0
