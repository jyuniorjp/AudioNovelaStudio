# TRASPASO — Audio Novela Studio (24-11-2026)

## 0. Mensaje para pegar en el chat nuevo

> Continúa el proyecto **Audio Novela Studio** (`D:\AudioNovelaStudio`, PC y Android). Lee `TRASPASO_AUDIONOVELA.md`
> y `HISTORIAL.txt` (secciones 1-5 y 8). Respóndeme siempre en español. El Estudio de Imágenes es un proyecto aparte
> (`D:\EstudioImagenes`) pero Audio Novela lleva una **copia integrada**: cambia los dos. PC y Android (APK) deben
> quedar iguales. Pregúntame antes de subir a GitHub o cerrar programas míos. Prefiero compilar yo con `COMPILAR.bat`.

## 1. Qué es
Programa para narrar fanfics, audionovelas y material de estudio con voces neurales (Edge) y voces sin internet;
detecta quién habla (Kuon IA) y le da su voz a cada personaje. Hace videos con imágenes IA por capítulo, miniatura
automática opcional, subtítulos (Whisper/Vosk), lectura de fórmulas para estudio, y trae el Estudio de Imágenes
integrado. Canal: Kuon: Ryūjin Jakka.
- PC: Python + customtkinter (`app.py`, `estudio.py`, `subtitulos.py`, `subs_ui.py`…) → `COMPILAR.bat`
  (último instalador: `instaladores\Instalar_AudioNovelaStudio_1.1.8.exe`, 24-11, al día con el código).
- Android: `Android\android\apk` (WebView; `assets\web\estudio.js`), APK 1.1.7 instalado en el Samsung A56;
  compilar con `compilar_apk_windows.sh` (usa `old107.apk`, `Android\terser`, `Android\buildvenv`).
- GitHub: **jyuniorjp/AudioNovelaStudio** (público, MIT; contiene las dos apps), releases v1.1.7 (instaladores + APK).
- Historia completa y reglas: `HISTORIAL.txt`. Skill de escritura en Claude: «texto-para-audio-novela-studio».

## 1b. Carpetas
- Raíz: el código (`*.py`, todos al mismo nivel porque se importan entre sí y PyInstaller los compila así),
  `COMPILAR.bat`, `ABRIR.bat` (abre el .exe o, si no hay, `app.py`), `AudioNovelaStudio.exe` (el último compilado),
  `README.md`, `LEEME*.txt`, `HISTORIAL.txt`, este traspaso.
- `instaladores\` el instalador nuevo que crea `COMPILAR.bat`; `instaladores\anteriores\` los viejos (solo local).
- `instalador\` (sin «es») el guion de Inno Setup y sus imágenes. `voz\` la voz sin internet (Piper).
- Datos que usa la app: `arte\`, `img\`, `fuentes\`, `ui_iconos\`, `iconos_imagenes\`, `ejemplos_ia\`, `docs\` (fotos del README).
- `notas\` estudios e informes (`ESTUDIO_GENERADORES_IMAGEN.md`, reports, research_notes, guía de prompts).
- `promocion\` video y portada de TikTok (solo local). `Android\` la APK y su compilación.
- Pruebas: `pruebas\` (tests de Kuon IA) y `archivos para pruevas\` (textos del usuario). `build\`/`dist\` se crean al compilar.
- Para Claude: `narrar.py` (narrar sin ventana, ver skill «ia-local» §5a) y `hardware.py` (tarjeta o procesador).

## 2. Estado y notas
- 1.1.8 (24-11): detección automática de tarjeta gráfica (`hardware.py`) y `narrar.py` / `--narrar`. La APK sigue
  en 1.1.7 (no cambia: estas novedades son solo de PC).
- Archivos solo locales (en .gitignore, no subir): `promo_tiktok_AudioNovelaStudio.mp4`, `portada_tiktok.png`,
  `TikTok_descripcion.txt`, `GUIA_PROMPTS_ESTUDIO.txt`.
- APIs del Asistente (Groq gpt-oss-120b, Gemini) configuradas en la app: **las claves nunca van a GitHub**.
- Subtítulos: modelos `faster-whisper-turbo` y `small` en `%APPDATA%\AudioNovelaStudio\modelos_voz` (restaurados
  desde la caché de Hugging Face) + librerías CUDA. Funciona en GPU.
- Imágenes de video: `_ai_backgrounds` usa una «ficha» de la historia por cuento (`fondos_ia\ficha_<hash>.json`),
  `poster_prompt` → Z-Image; modelo/estilo en Automático por defecto (se pueden cambiar a mano).
- Ejemplos de prompts del usuario (Hades/Hestia) en `%APPDATA%\AudioNovelaStudio\ejemplos_prompts.txt` (local).
- Hermes Chat (`D:\HermesChat`) es otra app aparte; ver su `TRASPASO_HERMES.md`.

## 3. PENDIENTES
1. Publicar el release 1.1.8 en GitHub (solo con permiso). La voz sin internet usa la tarjeta solo si se instala
   la versión GPU de sherpa-onnx (la de pip es solo procesador; hoy cae sola al procesador).
2. Mantener la copia integrada del Estudio igual a `D:\EstudioImagenes` (ver pendientes del Estudio: búsqueda web de
   personajes, recorte de imagen opaca, Qwen que inventa detalles).
3. Ideas en planificación (`HISTORIAL.txt`, sección 8): lectura de documentos de estudio (PDF digital y escaneado,
   tablas, fórmulas con MathCAT/SRE, OCR con Docling / PP-OCRv5 / ML Kit), arte anime final, APK de 32 bits.
4. Red neuronal «hermano menor» en pausa (sección 7): hacen falta ~1.500 diálogos reales anotados.
