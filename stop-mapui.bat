@echo off
rem stop-mapui.bat -- stop the mapui server (via its /api/shutdown endpoint).
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%mib2nds-tool\.venv\Scripts\python.exe" (
    set "PY=%ROOT%mib2nds-tool\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" "%ROOT%mib2nds-tool\stop_mapui.py" %*
