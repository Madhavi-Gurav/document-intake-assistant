@echo off
REM Double-click to set up and run the Document Intake Assistant.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 start.py %*) else (python start.py %*)
if errorlevel 1 pause
