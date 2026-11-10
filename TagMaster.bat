@echo off
setlocal
rem  TagMaster launcher / 打标高手 启动器
set "HERE=%~dp0"
set "PYW=%HERE%python\pythonw.exe"
set "PYX=%HERE%python\python.exe"

if exist "%PYW%" (
    start "" "%PYW%" "%HERE%TagMaster.py"
    exit /b 0
)
if exist "%PYX%" (
    start "" "%PYX%" "%HERE%TagMaster.py"
    exit /b 0
)
echo Python not found: %HERE%python
pause
