@echo off
rem Dispatch lifecycle and synchronization commands to Python on Windows.
setlocal DisableDelayedExpansion
set "command=%~1"
if not defined command goto invalid
if "%command%"=="--help" goto help
if "%command%"=="-h" goto help

rem Restrict dispatch to known entry points.
for %%C in (setup update record start sync-models sync-extensions sync-versions sync-config sync-tokens) do if "%command%"=="%%C" goto dispatch
goto invalid

:dispatch
set "python=%~dp0..\comfy-ui\.venv\Scripts\python.exe"
rem Setup alone can bootstrap a missing managed virtual environment.
if "%command%"=="setup" if not exist "%python%" set "python=python"

rem SHIFT does not modify %%*: remove the dispatcher argument from the original tail.
set "arguments=%*"
set "arguments=%arguments:* =%"
if "%*"=="%1" set "arguments="
"%python%" "%~dp0python\%command%.py" %arguments%
exit /b %errorlevel%

:help
call :usage
exit /b 0

:invalid
call :usage 1>&2
exit /b 2

:usage
echo Usage: comfy-sync.bat COMMAND [ARGUMENTS...]
echo Commands: setup update record start sync-models sync-extensions sync-versions sync-config sync-tokens
exit /b 0
