@echo off
setlocal
pushd "%~dp0"
if exist ".venv\Scripts\python.exe" goto pythonvenv
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto python312
py -3.11 --version >nul 2>&1
if not errorlevel 1 goto python311
python --version >nul 2>&1
if not errorlevel 1 goto pythondefault
echo Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/
set "lingjian_result=1"
goto finish
:pythonvenv
".venv\Scripts\python.exe" scripts\local_environment.py setup
goto result
:python312
py -3.12 scripts\local_environment.py setup
goto result
:python311
py -3.11 scripts\local_environment.py setup
goto result
:pythondefault
python scripts\local_environment.py setup
:result
set "lingjian_result=%errorlevel%"
:finish
if not "%lingjian_result%"=="0" echo Setup needs attention. Review the message above.
pause
popd
exit /b %lingjian_result%
