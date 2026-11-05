@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" scripts\local_environment.py start
set "lingjian_result=%errorlevel%"
if not "%lingjian_result%"=="0" pause
popd
exit /b %lingjian_result%
:missing
echo Run setup_windows.cmd first.
pause
popd
exit /b 1
