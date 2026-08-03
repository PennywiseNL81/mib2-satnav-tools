@echo off
rem setup.bat -- one-command setup: runs install.py inside this repo checkout.
setlocal
set "ROOT=%~dp0"
python "%ROOT%install.py" %*
