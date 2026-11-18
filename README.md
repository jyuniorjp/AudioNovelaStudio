# Audio Novela Studio + Estudio de Imágenes

Dos apps de escritorio para Windows, hechas en Python, que funcionan **en tu PC, sin internet**.

## Audio Novela Studio (1.1.7)
Convierte novelas, fanfics y documentos en audiolibros narrados con varias voces.
- Abre TXT, MD, DOCX, ODT, RTF, EPUB, FB2, HTML, PDF y subtítulos.
- **Kuon IA** reconoce los diálogos y quién habla, reparte las voces y lee cada frase con su emoción.
- Varios acentos del español, ajuste de dicción por obra, línea de tiempo y caché para generar al instante.
- Exporta audio y video (con fondos generados por IA para cada capítulo).

## Estudio de Imágenes (1.0.0)
Crea imágenes con IA en tu PC usando una tarjeta NVIDIA (ComfyUI escondido como motor).
- **Automático:** tu IA local (Qwen, vía Ollama) lee lo que pides y elige el modelo: Animagine XL 4 para anime
  y personajes conocidos, Z-Image Turbo para fotos, arte y texto, FLUX opcional.
- **Revisión:** Gemma 4 (la IA que ve) revisa que cada imagen tenga lo pedido y la repite si falta algo.
- **Miniaturas de YouTube por capas:** fondo, personajes recortados (pantalla de color + BiRefNet), título con
  letras perfectas donde y como lo pidas, versiones A/B/C y editor por capas con atajos de teclado.
- Imágenes de referencia, ampliar 2x/4x, quitar fondo, guardar en cualquier formato.

## Compilar
1. Python 3.12+ y un entorno `.venv` con `pip install -r requirements.txt`.
2. `COMPILAR.bat` crea `AudioNovelaStudio.exe` y su instalador (Inno Setup 6).
3. `COMPILAR_IMAGENES.bat` crea `EstudioImagenes.exe` y su instalador.

El Estudio de Imágenes descarga el motor y los modelos la primera vez (te dice cuánto pesa cada cosa).

## Qué no está en este repositorio
- La versión de Android (todavía no se publica).
- Modelos grandes de terceros: la voz neuronal Piper `es_MX-claude-high`
  ([HirCoir/Piper-TTS-Spanish](https://huggingface.co/spaces/HirCoir/Piper-TTS-Spanish), Apache-2.0) va en `voz/`.
- Instaladores y ejecutables compilados.

Más detalles de uso en [LEEME.txt](LEEME.txt) y [LEEME_IMAGENES.txt](LEEME_IMAGENES.txt).

## Licencia
MIT: puedes usar, copiar y modificar este código mientras mantengas el aviso de autor. Ver [LICENSE](LICENSE). Las fuentes de `fuentes/` usan la licencia SIL OFL 1.1.
