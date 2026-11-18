# -*- coding: utf-8 -*-
"""OCR (reconocer texto en PDF escaneados y fotos de apuntes) con el OCR que ya trae Windows, sin internet y sin
peso extra. Idioma: español (es-MX o es-ES). Las páginas se dibujan con pypdfium2.
En el teléfono se usa la IA del usuario (Gemini) porque Android no trae un OCR que la APK pueda usar sin recompilar."""
import asyncio
import io

PAGE_SCALE = 2.6          # ~190 ppp: buen equilibrio entre acierto y velocidad


def available():
    try:
        from winrt.windows.media.ocr import OcrEngine
        return any(l.language_tag.lower().startswith("es") for l in OcrEngine.available_recognizer_languages)
    except Exception:
        return False


def _engine():
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    langs = [l.language_tag for l in OcrEngine.available_recognizer_languages]
    tag = next((t for t in langs if t.lower() == "es-mx"), None) or next((t for t in langs if t.lower().startswith("es")), None)
    eng = OcrEngine.try_create_from_language(Language(tag)) if tag else OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise RuntimeError("Windows no tiene el reconocimiento de texto en español. Instálalo en Configuración > Hora e "
                           "idioma > Idioma > Español > Opciones > Reconocimiento óptico de caracteres.")
    return eng


async def _ocr_png(eng, png):
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    from winrt.windows.graphics.imaging import BitmapDecoder
    stream = InMemoryRandomAccessStream()
    w = DataWriter(stream)
    w.write_bytes(png)
    await w.store_async()
    await w.flush_async()
    w.detach_stream()
    stream.seek(0)
    dec = await BitmapDecoder.create_async(stream)
    bmp = await dec.get_software_bitmap_async()
    res = await eng.recognize_async(bmp)
    return "\n".join(line.text for line in res.lines)


def _png(img, limit=9000):
    from PIL import Image
    img = img.convert("L")                               # gris: mejor contraste para el OCR
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def ocr_image(path):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path))      # fotos del celular giradas
    eng = _engine()
    return asyncio.run(_ocr_png(eng, _png(img)))


def ocr_pdf(path, pages=None, progress=None, cancel=None):
    """Texto de las páginas indicadas (todas si pages es None). progress(hecho, total)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    idx = list(range(len(pdf))) if pages is None else list(pages)
    eng = _engine()
    out = {}

    async def run():
        for k, i in enumerate(idx):
            if cancel is not None and cancel.is_set():
                break
            page = pdf[i]
            img = page.render(scale=PAGE_SCALE).to_pil()
            out[i] = await _ocr_png(eng, _png(img))
            if progress:
                progress(k + 1, len(idx))
    asyncio.run(run())
    pdf.close()
    return out
