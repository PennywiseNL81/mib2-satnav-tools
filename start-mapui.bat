@echo off
rem start-mapui.bat -- start the MIB2 map UI (uses the venv if present).
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%mib2nds-tool\.venv\Scripts\python.exe" (
    set "PY=%ROOT%mib2nds-tool\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" "%ROOT%mib2nds-tool\mapui.py" %*
