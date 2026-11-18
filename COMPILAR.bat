@echo off
setlocal
chcp 65001 >nul
title Audio Novela Studio - Compilando
cd /d "%~dp0"
set "LOG=%~dp0build_log.txt"
echo === Compilacion %date% %time% === > "%LOG%"

echo [1/6] Buscando Python...
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python no esta instalado. Instalando Python 3.12 con winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo ERROR: no se pudo encontrar ni instalar Python. >> "%LOG%"
  echo BUILD_FAIL >> "%LOG%"
  echo ERROR: instala Python desde python.org y vuelve a ejecutar este archivo.
  timeout /t 30
  exit /b 1
)
%PY% --version >> "%LOG%" 2>&1

echo [2/6] Preparando entorno aislado (.venv)...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv >> "%LOG%" 2>&1
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo ERROR: no se pudo crear .venv >> "%LOG%"
  echo BUILD_FAIL >> "%LOG%"
  timeout /t 30
  exit /b 1
)

echo [3/6] Instalando dependencias (voces, IA, audio, interfaz)...
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install --upgrade -r requirements.txt pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 (
  echo ERROR instalando dependencias >> "%LOG%"
  echo BUILD_FAIL >> "%LOG%"
  echo ERROR instalando dependencias. Revisa build_log.txt
  timeout /t 30
  exit /b 1
)

echo [4/6] Probando los modulos...
"%VPY%" -c "import brain, audio, voices, docs, localai, assistant, assistant_ui, estudio, repaso, ocr, video, ia_dudosos, subtitulos, subs_ui, asr_worker, imagen_ia, imagenes_app, instalar_ia, editor_miniaturas, miniaturas, miniatura_auto, web_ayuda, websockets.sync.client, vosk, faster_whisper, ctranslate2, pypdfium2, customtkinter, tkinterdnd2, numpy, miniaudio, lameenc, pygame, edge_tts, sherpa_onnx; from winrt.windows.media.ocr import OcrEngine; print('IMPORT_OK')" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo ERROR: falta un modulo >> "%LOG%"
  echo BUILD_FAIL >> "%LOG%"
  echo ERROR probando modulos. Revisa build_log.txt
  timeout /t 30
  exit /b 1
)

echo [5/6] Compilando AudioNovelaStudio.exe...
taskkill /im AudioNovelaStudio.exe /f >nul 2>&1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name AudioNovelaStudio --icon icon.ico --add-data "icon.ico;." --add-data "kuon_base.json;." --add-data "kuon_dict.json;." --add-data "img;img" --collect-all customtkinter --collect-all tkinterdnd2 --collect-all miniaudio --collect-all sherpa_onnx --collect-all pypdfium2 --collect-all vosk --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all tokenizers --collect-all av --hidden-import asr_worker --hidden-import imagenes_app --hidden-import instalar_ia --hidden-import editor_miniaturas --hidden-import miniaturas --hidden-import web_ayuda --hidden-import miniatura_auto --hidden-import websockets.sync.client --add-data "fuentes;fuentes" --add-data "quitar_fondo.py;." --add-data "estudio_imagenes.ico;." --add-data "ejemplos_ia;ejemplos_ia" --collect-all pypdfium2_raw --collect-submodules winrt --collect-all winrt --hidden-import _cffi_backend --collect-data certifi app.py >> "%LOG%" 2>&1
if not exist "dist\AudioNovelaStudio.exe" (
  echo ERROR: PyInstaller no genero el exe >> "%LOG%"
  echo BUILD_FAIL >> "%LOG%"
  echo ERROR al compilar. Revisa build_log.txt
  timeout /t 30
  exit /b 1
)
copy /y "dist\AudioNovelaStudio.exe" "%~dp0AudioNovelaStudio.exe" >> "%LOG%" 2>&1
if exist core.py del /q core.py
if not exist "Audios" mkdir "Audios"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Audio Novela Studio.lnk')); $s.TargetPath='%~dp0AudioNovelaStudio.exe'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0AudioNovelaStudio.exe,0'; $s.Save()" >> "%LOG%" 2>&1

echo [6/6] Creando el instalador para otras PC...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC goto :tengo_iscc
echo    Inno Setup no esta instalado: instalandolo con winget...
winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC goto :tengo_iscc
echo    Sin Inno Setup: el programa funciona igual, pero no se creo el instalador.
echo    Descargalo gratis de jrsoftware.org/isdl.php y vuelve a ejecutar este archivo.
echo INSTALLER_SKIP >> "%LOG%"
goto :fin_instalador

:tengo_iscc
"%VPY%" -c "import version;print(version.APP_VERSION['version'])" > "%TEMP%\ans_ver.txt"
set /p APPVER=<"%TEMP%\ans_ver.txt"
if not defined APPVER set "APPVER=1.0.0"
if exist "Instalar_AudioNovelaStudio_%APPVER%.exe" del /q "Instalar_AudioNovelaStudio_%APPVER%.exe"
"%ISCC%" /Q "/DAppVer=%APPVER%" "instalador\AudioNovelaStudio.iss" >> "%LOG%" 2>&1
if exist "Instalar_AudioNovelaStudio_%APPVER%.exe" goto :instalador_ok
echo    No se pudo crear el instalador. Revisa build_log.txt
echo INSTALLER_FAIL >> "%LOG%"
goto :fin_instalador
:instalador_ok
echo    Listo: Instalar_AudioNovelaStudio_%APPVER%.exe
echo INSTALLER_OK >> "%LOG%"
:fin_instalador

echo BUILD_OK >> "%LOG%"
echo.
echo Listo. Abriendo Audio Novela Studio...
start "" "%~dp0AudioNovelaStudio.exe"
timeout /t 8
