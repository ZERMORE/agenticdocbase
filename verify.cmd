@echo off
setlocal
set PYTHONUTF8=1
pushd "%~dp0doc2md"
".conda\python.exe" -m pytest -q
if errorlevel 1 (popd & exit /b 1)
popd
pushd "%~dp0DocBase"
".conda\python.exe" -m pytest -q
if errorlevel 1 (popd & exit /b 1)
popd
pushd "%~dp0DocAgent"
".conda\python.exe" -m pytest -q
if errorlevel 1 (popd & exit /b 1)
popd
pushd "%~dp0ResearchDesk\service"
".conda\python.exe" -m pytest -q
if errorlevel 1 (popd & exit /b 1)
popd
pushd "%~dp0ResearchDesk\web"
call npm.cmd run build
set result=%errorlevel%
popd
exit /b %result%
