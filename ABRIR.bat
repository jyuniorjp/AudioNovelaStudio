@echo off
cd /d "%~dp0"
if exist "AudioNovelaStudio.exe" (start "" "AudioNovelaStudio.exe" %* & exit /b)
if exist ".venv\Scripts\pythonw.exe" (start "" ".venv\Scripts\pythonw.exe" app.py %* & exit /b)
echo Primero ejecuta COMPILAR.bat
pause
