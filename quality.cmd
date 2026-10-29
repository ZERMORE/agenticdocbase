@echo off
setlocal
set PYTHONUTF8=1
"%~dp0DocBase\.conda\python.exe" -X utf8 "%~dp0workflows\quality_eval.py" %*
exit /b %errorlevel%
