@echo off
rem Start the task board server, then open it in the browser (Windows). Extra arguments go to server.py.
rem Prefers the py launcher: a bare "python" can be the Microsoft Store stub, which runs nothing.
setlocal
set "LAUNCH=%~dp0..\launch.py"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%LAUNCH%" %*
  exit /b
)
python -c "import sys; sys.exit(sys.version_info < (3, 7))" >nul 2>nul
if errorlevel 1 (
  echo onemoretask needs Python 3.7 or newer. Install it from https://www.python.org/downloads/ 1>&2
  echo or run: winget install Python.Python.3.12 1>&2
  exit /b 1
)
python "%LAUNCH%" %*
