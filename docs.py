# -*- coding: utf-8 -*-
"""Lectura de documentos, capítulos, limpieza y diccionario de pronunciación."""
import json
import os
import re
import sys
import unicodedata


# ---------------------------------------------------------------------------
# Diccionarios de Kuon IA (kuon_dict.json): nombres con género, palabras comunes,
# verbos de habla y pronunciación inglesa
# ---------------------------------------------------------------------------

def _load_dict():
    for d in (os.path.dirname(os.path.abspath(__file__)), getattr(sys, "_MEIPASS", None)):
        p = os.path.join(d, "kuon_dict.json") if d else ""
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
    return {}


DICT = _load_dict()
DICT_F = set(DICT.get("nombres_f", "").split())
DICT_M = set(DICT.get("nombres_m", "").split())
DICT_COMMON = set(DICT.get("comunes", "").split())
DICT_SPEECH = DICT.get("habla", "").split()
EN_PRON = DICT.get("ingles", {})


def plain(s):
    """Minúsculas sin tildes: 'José' -> 'jose'."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")



def _decode(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_MAGIC = ((b"\x89PNG", "una imagen"), (b"\xff\xd8\xff", "una imagen"), (b"GIF8", "una imagen"),
          (b"ID3", "un audio"), (b"OggS", "un audio"), (b"fLaC", "un audio"),
          (b"MZ", "un programa"), (b"Rar!", "un archivo comprimido"), (b"7z\xbc\xaf", "un archivo comprimido"),
          (b"\x1f\x8b", "un archivo comprimido"), (b"\x1aE\xdf\xa3", "un video"))


def not_text_kind(raw):
    """Devuelve qué parece el archivo si no es texto (imagen, audio, video…), o '' si es texto."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[:3] == b"\xef\xbb\xbf":
        return ""
    for sig, kind in _MAGIC:
        if kind and raw.startswith(sig):
            return kind
    if raw[:4] == b"RIFF":
        return "una imagen" if raw[8:12] == b"WEBP" else ("un video" if raw[8:12] == b"AVI " else "un audio")
    if raw[4:8] == b"ftyp":
        return "un video o audio"
    if raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "un audio"
    head = raw[:65536]
    if head:
        ctrl = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13))
        if ctrl / len(head) > 0.05:
            return "un archivo binario"
    return ""


IMAGE_EXT = ("png", "jpg", "jpeg", "bmp", "tif", "tiff", "gif")


class NeedsOCR(Exception):
    """El documento es una imagen o un PDF escaneado: hace falta reconocer el texto (ocr.py)."""
    def __init__(self, path, kind):
        super().__init__("El PDF no tiene texto (parece escaneado como imagen)." if kind == "pdf"
                         else "Es una imagen: hace falta reconocer el texto.")
        self.path, self.kind = path, kind


def read_document(path):
    """Lee casi cualquier formato de texto y devuelve texto limpio para narrar."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    raw = open(path, "rb").read()
    if raw[:4] == b"%PDF":
        t = pdf_text(path)
        return common(t)
    if raw[:4] == b"\xd0\xcf\x11\xe0":
        raise ValueError("Los .doc antiguos de Word no se pueden leer. Ábrelo en Word o WPS y guárdalo como .docx.")
    if raw[:2] == b"PK":
        import zipfile, io
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
            files = {n: _decode(z.read(n)) for n in z.namelist()
                     if re.search(r"\.(xml|xhtml|html?|opf|ncx|fb2|txt|md)$", n, re.I)}
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, ValueError, RuntimeError):
            raise ValueError("El archivo está dañado o incompleto (no se pudo abrir por dentro). "
                             "Ábrelo en Word o WPS y guárdalo otra vez, o vuelve a descargarlo.")
        t = from_zip(files)
        if not t.strip():
            raise ValueError("Este archivo comprimido no tiene texto que se pueda narrar.")
        return unwrap(common(t))
    kind = not_text_kind(raw)
    if kind == "una imagen" and ext in IMAGE_EXT:
        raise NeedsOCR(path, "image")                # foto de apuntes o de una página: la app ofrece el OCR
    if kind:
        raise ValueError(f"Este archivo no es un documento de texto (parece {kind}). "
                         f"Abre un documento: {FORMATS}.")
    t = _decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    head = t[:400].lstrip()
    if ext == "rtf" or head.startswith("{\\rtf"):
        t = rtf_text(t)
    elif ext in ("tex", "latex") or r"\begin{document}" in t[:20000]:
        import estudio
        t = estudio.latex_document_text(t)
    elif ext in ("srt", "vtt", "ass", "ssa", "sub"):
        t = subtitles_text(t, ext)
    elif ext == "fb2" or "<FictionBook" in head:
        t = fb2_text(t)
    elif ext in ("html", "htm", "xhtml", "mhtml") or re.match(r"<(!doctype html|html\b)", head, re.I):
        t = html_text(t)
    elif ext in ("md", "markdown", "mdown") or re.search(r"^#{1,3}\s|\*\*[^*]+\*\*", t[:4000], re.M):
        t = markdown_text(t)
    return unwrap(common(t))


# ---------------------------------------------------------------------------
# Importador (mismo criterio que la app de Android)
# ---------------------------------------------------------------------------

def common(t):
    t = t.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    # transcripciones de YouTube: "(05:10)", "[música]", "[aplausos]"
    t = re.sub(r"\(\d{1,2}:\d{2}(?::\d{2})?\)\s*", "", t)
    t = re.sub(r"\[(?:música|music|aplausos|applause|risas|laughter|sonido|ruido|silencio)\]\s*", "", t, flags=re.I)
    t = re.sub(r"[\u200b-\u200d\u2060\ufeff\u00ad]", "", t)
    t = re.sub(r"[\u00a0\u2007\u202f]", " ", t).replace("\t", " ")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", t)
    # índices: «Objetivo General . . . . . . 2» o «Resumen ........ 4» -> «Objetivo General» (la voz no lee puntitos)
    t = re.sub(r"[ \t]*(?:\.[ \t]+){3,}\.?[ \t]*\d{0,4}(?:[ \t]+[ivxlc]{1,5})?[ \t]*$", "", t, flags=re.M)
    t = re.sub(r"[ \t]*\.{6,}[ \t]*\d{1,4}(?:[ \t]+[ivxlc]{1,5})?[ \t]*$", "", t, flags=re.M)
    t = re.sub(r"[ ]{2,}", " ", t)
    t = re.sub(r" +\n", "\n", t)
    t = re.sub(r"\n +", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


_ENDS = re.compile(r"[.!?…:;»”\"')\]]$")
_DLG = re.compile(r"^[—–\-«\"“'‘]")
_LOW = re.compile(r"^[a-záéíóúñüàèìòùç]")


def _looks_wrapped(lines):
    cand = n = 0
    for a, b in zip(lines, lines[1:]):
        if not a or not b:
            continue
        n += 1
        if len(a) > 35 and not _ENDS.search(a) and _LOW.match(b):
            cand += 1
    return n > 20 and cand / n > 0.18


def unwrap(t, force=False):
    """Une líneas cortadas a mano (PDF, TXT con salto fijo) sin tocar los diálogos."""
    lines = t.split("\n")
    if not force and not _looks_wrapped(lines):
        return t
    out, i = [], 0
    while i < len(lines):
        a = lines[i]
        while i + 1 < len(lines):
            b = lines[i + 1]
            if not a or not b or _DLG.match(b):
                break
            if re.search(r"\w-$", a) and _LOW.match(b):
                a, i = a[:-1] + b, i + 1
                continue
            if not _ENDS.search(a) and (_LOW.match(b) or a.endswith(",")):
                a, i = a + " " + b, i + 1
                continue
            break
        out.append(a)
        i += 1
    return "\n".join(out)


def markdown_text(t):
    t = re.sub(r"^```[\s\S]*?^```", "", t, flags=re.M)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", r"\n\1\n", t, flags=re.M)
    t = re.sub(r"^\s{0,3}>\s?", "", t, flags=re.M)
    t = re.sub(r"^\s*[*+]\s+", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(^|[\s(—«\"“])\*(?!\s)([^*\n]+?)\*(?=[\s).,;:!?…»”\"]|$)", r"\1\2", t, flags=re.M)
    t = re.sub(r"(^|[\s(—«\"“])_(?!\s)([^_\n]+?)_(?=[\s).,;:!?…»”\"]|$)", r"\1\2", t, flags=re.M)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"^\|(.*)\|\s*$", lambda m: "" if re.match(r"^[\s|:-]+$", m.group(1)) else m.group(1).replace("|", " · "),
               t, flags=re.M)
    return re.sub(r"<[^>\n]+>", "", t)


def html_text(h):
    import html as _html
    h = re.sub(r"<!--[\s\S]*?-->", "", h)
    h = re.sub(r"<(script|style|head|nav|svg|math|aside)\b[\s\S]*?</\1>", "", h, flags=re.I)
    h = re.sub(r"<(br|hr)\b[^>]*>", lambda m: "\n\n* * *\n\n" if m.group(1).lower() == "hr" else "\n", h, flags=re.I)
    h = re.sub(r"</?(p|div|h[1-6]|li|tr|blockquote|section|article|header|footer|figure|figcaption|dt|dd|pre|table|ul|ol)\b[^>]*>",
               "\n\n", h, flags=re.I)
    h = re.sub(r"</?(td|th)\b[^>]*>", " ", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"[ \t]+", " ", _html.unescape(h))


_RTF_SKIP = re.compile(r"^(fonttbl|colortbl|stylesheet|info|pict|header|footer|headerl|headerr|footerl|footerr|object|"
                       r"themedata|colorschememapping|latentstyles|datastore|xmlnstbl|listtable|listoverridetable|"
                       r"rsidtbl|generator|filetbl|revtbl|mmathPr|pgdsctbl)$")
_RTF_MAP = {"par": "\n", "line": "\n", "sect": "\n\n", "page": "\n\n", "tab": " ", "emdash": "—", "endash": "–",
            "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”", "bullet": "•"}
_RTF_TOK = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}\r\n]+)|[\r\n]")


def rtf_text(s):
    if not s.lstrip().startswith("{\\rtf"):
        return s
    out, stack, skip, uc, skip_n, hexbuf = [], [], False, 1, 0, bytearray()

    def flush():
        if hexbuf:
            out.append(hexbuf.decode("cp1252", errors="replace"))
            hexbuf.clear()
    for m in _RTF_TOK.finditer(s):
        word, num, hx, sym, brace, txt = m.groups()
        if hx:
            if not skip:
                if skip_n:
                    skip_n -= 1
                else:
                    hexbuf.append(int(hx, 16))
            continue
        flush()
        if brace == "{":
            stack.append((skip, uc))
            if s.startswith("\\*", m.end()):
                skip = True
        elif brace == "}":
            skip, uc = stack.pop() if stack else (False, 1)
        elif sym:
            if not skip:
                out.append({"~": " ", "_": "-", "\\": "\\", "{": "{", "}": "}", "\n": "\n", "\r": "\n"}.get(sym, ""))
        elif word:
            if _RTF_SKIP.match(word):
                skip = True
            elif skip:
                pass
            elif word == "uc":
                uc = int(num or 1)
            elif word == "u":
                cp = int(num or 0)
                out.append(chr(cp + 65536 if cp < 0 else cp))
                skip_n = uc
            elif word in _RTF_MAP:
                out.append(_RTF_MAP[word])
        elif txt and not skip:
            if skip_n:
                k = min(skip_n, len(txt))
                txt, skip_n = txt[k:], skip_n - k
            out.append(txt)
    flush()
    return "".join(out)


def subtitles_text(t, ext):
    if ext in ("ass", "ssa"):
        rows = [",".join(l.split(",")[9:]) for l in t.split("\n") if l.lower().startswith("dialogue:")]
        return "\n".join(re.sub(r"\{[^}]*\}", "", r).replace("\\N", "\n").replace("\\n", "\n") for r in rows)
    out, block = [], []
    for l in t.split("\n"):
        s = l.strip()
        if not s:
            if block:
                out.append(re.sub(r"<[^>]+>", "", " ".join(block)))
                block = []
            continue
        if s.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) or s.isdigit() or "-->" in s:
            continue
        block.append(s)
    if block:
        out.append(re.sub(r"<[^>]+>", "", " ".join(block)))
    return "\n".join(out)


def fb2_text(x):
    import xml.etree.ElementTree as ET
    x = re.sub(r"<binary\b[\s\S]*?</binary>", "", x)
    try:
        root = ET.fromstring(x.encode("utf-8"))
    except ET.ParseError:
        return html_text(x)
    out = []

    def tag(e):
        return e.tag.split("}")[-1]

    def walk(n):
        for c in n:
            t = tag(c)
            if t in ("binary", "description", "notes"):
                continue
            if t in ("p", "v", "subtitle", "text-author"):
                out.append("".join(c.itertext()).strip())
            elif t == "empty-line":
                out.append("")
            elif t == "title":
                out.extend(["", re.sub(r"\s+", " ", " ".join(c.itertext())).strip(), ""])
            else:
                walk(c)
    for b in root.iter():
        if tag(b) == "body" and b.get("name") != "notes":
            walk(b)
    return "\n".join(out)


def _xml_txt(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _docx_tables(x):
    """Cada tabla de Word -> párrafos «Tabla n. Fila 1, …» para que se lea fila por fila."""
    import estudio
    n = [0]

    def one(m):
        rows = []
        for tr in re.findall(r"<w:tr\b[\s\S]*?</w:tr>", m.group(0)):
            rows.append([_docx_xml(tc, tables=False).replace("\n", " ").strip()
                         for tc in re.findall(r"<w:tc\b[\s\S]*?</w:tc>", tr)])
        n[0] += 1
        lines = estudio.table_lines(rows, n[0])
        return "".join(f"<w:p><w:r><w:t>{_xml_txt(l)}</w:t></w:r></w:p>" for l in lines)
    return re.sub(r"<w:tbl\b[\s\S]*?</w:tbl>", one, x)


def _docx_xml(x, tables=True):
    import html as _html
    import estudio
    x = re.sub(r"<w:del\b[\s\S]*?</w:del>", "", x)
    x = re.sub(r"<w:instrText[\s\S]*?</w:instrText>", "", x)
    # ecuaciones de Word: antes se perdían; ahora quedan como texto lineal («d²y/dx²», «√(x)») que la voz entiende
    x = re.sub(r"<m:oMathPara\b[\s\S]*?</m:oMathPara>|<m:oMath\b[\s\S]*?</m:oMath>",
               lambda m: "<w:t> " + _xml_txt(estudio.omml_to_linear(m.group(0))) + " </w:t>", x)
    if tables:
        x = _docx_tables(x)
    out = []
    for p in x.split("</w:p>"):
        line = []
        for m in re.finditer(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|<w:tab\s*/>|<w:br\b[^>]*/>|<w:cr\s*/>|<w:noBreakHyphen\s*/>", p):
            if m.group(1) is not None:
                line.append(m.group(1))
            elif m.group(0).startswith("<w:tab"):
                line.append(" ")
            elif m.group(0).startswith("<w:noBreak"):
                line.append("-")
            else:
                line.append("\n")
        out.append("".join(line))
    return _html.unescape("\n".join(out))


def _odt_xml(x):
    import html as _html
    x = re.sub(r"<text:note\b[\s\S]*?</text:note>", "", x)
    x = re.sub(r"<office:annotation\b[\s\S]*?</office:annotation>", "", x)
    x = re.sub(r'<text:s(?:\s+text:c="(\d+)")?\s*/>', lambda m: " " * int(m.group(1) or 1), x)
    x = re.sub(r"<text:(tab|line-break)\s*/>", lambda m: " " if m.group(1) == "tab" else "\n", x)
    x = re.sub(r"</text:(p|h)>", "\n", x)
    return _html.unescape(re.sub(r"<[^>]+>", "", x))


def _resolve(base, href):
    from urllib.parse import unquote
    parts = (base.split("/")[:-1] if base else []) + unquote(href.split("#")[0]).split("/")
    out = []
    for p in parts:
        if p == "..":
            if out:
                out.pop()
        elif p and p != ".":
            out.append(p)
    return "/".join(out)


def _epub(files):
    cont = files.get("META-INF/container.xml", "")
    m = re.search(r'full-path="([^"]+)"', cont)
    opf_path = m.group(1) if m else next((k for k in files if k.lower().endswith(".opf")), "")
    opf = files.get(opf_path, "")
    items = {}
    for a in re.findall(r"<(?:opf:)?item\b([^>]*)/?>", opf):
        i, h = re.search(r'\bid="([^"]+)"', a), re.search(r'\bhref="([^"]+)"', a)
        pr = re.search(r'\bproperties="([^"]+)"', a)
        if i and h:
            items[i.group(1)] = (_resolve(opf_path, h.group(1)), bool(pr and re.search(r"\bnav\b", pr.group(1))))
    order = []
    for a in re.findall(r"<(?:opf:)?itemref\b([^>]*)/?>", opf):
        i, lin = re.search(r'\bidref="([^"]+)"', a), re.search(r'\blinear="([^"]+)"', a)
        if i and i.group(1) in items and not items[i.group(1)][1] and not (lin and lin.group(1) == "no"):
            order.append(items[i.group(1)][0])
    docs_ = order or sorted(k for k in files if re.search(r"\.x?html?$", k, re.I))
    return "\n\n".join(html_text(files[h]) if h in files else "" for h in docs_)


def from_zip(files):
    if "word/document.xml" in files:
        return _docx_xml(files["word/document.xml"])
    if "content.xml" in files and "office:document-content" in files["content.xml"]:
        return _odt_xml(files["content.xml"])
    if "META-INF/container.xml" in files or any(k.lower().endswith(".opf") for k in files):
        return _epub(files)
    fb = next((k for k in files if k.lower().endswith(".fb2")), None)
    if fb:
        return fb2_text(files[fb])
    txt = sorted(k for k in files if re.search(r"\.(txt|md)$", k, re.I))
    if txt:
        return "\n\n".join(files[k] for k in txt)
    raise ValueError("Este archivo comprimido no tiene un documento de texto reconocible.")


_ONE_LETTER = set("a e o u y de la el en lo los las le les se si sí no ni ya al del un una que por con para "
                  "su sus mi mis tu tus te me nos es son era fue ha he va da di vi ve".split())
_PLAIN_COMMON = None
_TOK = re.compile(r"[^\W\d_]+$")


def fix_split_words(line):
    """Une palabras que el PDF parte por el espaciado del diseño: «F ACULTAD» -> «FACULTAD»,
    «FORMA TIV A» -> «FORMATIVA». Solo une si el resultado es una palabra conocida y alguna parte no lo es,
    así «a la» o «de sea» no se tocan."""
    global _PLAIN_COMMON
    if " " not in line:
        return line
    if _PLAIN_COMMON is None:
        _PLAIN_COMMON = {plain(w) for w in DICT_COMMON}
    known = lambda w: plain(w) in _PLAIN_COMMON or plain(w) in _ONE_LETTER
    toks = line.split(" ")
    out, i = [], 0
    while i < len(toks):
        merged = False
        for j in range(min(len(toks), i + 5), i + 1, -1):
            part = toks[i:j]
            if not all(_TOK.match(p) for p in part):
                continue
            # solo títulos en mayúsculas («F ACULTAD») o una mayúscula suelta + minúsculas («F acultad»):
            # en la prosa normal en minúsculas no se toca nada
            caps = all(p.isupper() for p in part) or (len(part[0]) == 1 and part[0].isupper()
                                                      and all(p.islower() for p in part[1:]))
            if caps and plain("".join(part)) in _PLAIN_COMMON and not all(known(p) for p in part):
                out.append("".join(part))
                i, merged = j, True
                break
        if not merged:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("Para abrir PDF hace falta el módulo pypdf. Vuelve a ejecutar COMPILAR.bat.")
    try:
        r = PdfReader(path)
        if r.is_encrypted and not r.decrypt(""):
            raise ValueError("El PDF está protegido con contraseña. Ábrelo, quítale la contraseña y vuelve a intentarlo.")
        pg_list = list(r.pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError("El PDF está dañado o incompleto. Prueba a descargarlo de nuevo o a guardarlo otra vez "
                         "como PDF desde el programa donde lo hiciste.")
    pages = []
    for pg in pg_list:
        try:
            t = pg.extract_text() or ""
        except Exception:
            t = ""
        pages.append([fix_split_words(l.strip()) for l in t.split("\n") if l.strip()])
    from collections import Counter
    key = lambda l: re.sub(r"\d+", "#", l).strip()
    freq = Counter()
    for ls in pages:
        for l in {key(x) for x in ls[:2] + ls[-2:]}:
            freq[l] += 1
    lim = max(3, len(pages) * 0.4)
    out = []
    for ls in pages:
        keep = [l for i, l in enumerate(ls)
                if not re.match(r"^(p[aá]g(ina)?\.?\s*)?\d{1,4}(\s*(de|of|/)\s*\d{1,4})?$", l, re.I)
                and not ((i < 2 or i >= len(ls) - 2) and len(l) < 60 and freq[key(l)] >= lim and not _DLG.match(l))]
        out.append("\n".join(keep))
    t = "\n".join(out)
    if not t.strip():
        raise NeedsOCR(path, "pdf")
    return unwrap(t, True)


FORMATS = "TXT, MD, DOCX, ODT, RTF, EPUB, FB2, HTML, PDF, LaTeX (.tex) y subtítulos (SRT, VTT, ASS)"


SUPPORTED = (".txt", ".docx", ".md", ".text", ".markdown", ".odt", ".rtf", ".epub", ".fb2", ".html", ".htm", ".xhtml",
             ".pdf", ".srt", ".vtt", ".ass", ".ssa", ".tex")

# «Capítulo», «Prólogo»… bastan solos. «Parte», «Acto», «Escena»… son palabras comunes («Acto seguido, …»,
# «Parte de mí…»): cuentan como título solo con número, romano o «:» detrás, y si la línea no acaba en punto.
CHAPTER_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:\*\*)?(?:"
    r"(?:cap[ií]tulo|cap\.|chapter|pr[oó]logo|ep[ií]logo|interludio)\b[^\n]{0,90}"
    r"|(?:escena|acto|parte|arco|volumen|extra)(?:\s*$|\s*[:\-–—]|\s+(?:\d+|[IVXLC]+|uno|dos|tres|cuatro|cinco|"
    r"seis|siete|ocho|nueve|diez|final|especial|post|bonus|adicional|eliminada|perdida)\b)[^\n]{0,80}(?<![.,;])"
    r")$",
    re.IGNORECASE | re.MULTILINE,
)
BREAK_RE = re.compile(r"^\s*(?:[*~#=_\-·•◇◆✦❖]\s*){3,}$")


def split_chapters(text):
    """[(titulo, inicio, fin)] sobre el texto original."""
    marks = list(CHAPTER_RE.finditer(text))
    if not marks:
        return [("Texto completo", 0, len(text))]
    out = []
    if text[: marks[0].start()].strip():
        out.append(("Inicio", 0, marks[0].start()))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        title = re.sub(r"[#*_]", "", m.group(0)).strip()
        out.append((title, m.start(), end))
    return out


def clean_for_speech(t):
    """Quita marcas de formato que la voz leería en alto."""
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`~#>]+", "", t)
    t = re.sub(r"\[(?:[^\]]{0,40})\]\((?:[^)]*)\)", "", t)
    t = t.replace("…", "...").replace(" ", " ")
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def parse_lexicon(src):
    """Líneas 'Palabra = cómo se dice'. Devuelve lista de (regex, reemplazo)."""
    out = []
    for line in (src or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        a, b = line.split("=", 1)
        a, b = a.strip(), b.strip()
        if a:
            out.append((re.compile(r"(?<!\w)" + re.escape(a) + r"(?!\w)"), b))
    return out


def apply_lexicon(text, lex):
    for rx, rep in lex:
        text = rx.sub(rep, text)
    return text


DEFAULT_LEXICON = """# Diccionario de pronunciación: Palabra = cómo debe sonar
# Útil para nombres de anime, ataques y lugares.
Ryūjin = Riuyín
Jakka = Yaca
Shingeki = Shinguéki
Kyojin = Kioyin
"""


# ---------------------------------------------------------------------------
# Detector de pronunciación: palabras que una voz en español podría leer "en inglés"
# ---------------------------------------------------------------------------

EN_WORDS = set("""
love god dark light king queen shadow fire blood star moon sun knight death soul heart black white
dragon lord master angel devil demon night storm ice wolf rose gold silver iron steel stone sky sea
war blade sword moon dream world time life lady boy girl man men woman sweet honey baby kiss lover
end hell heaven holy ghost spirit dead cold hot red blue green gray grey rain snow wind thunder
blue break broken lost hope faith grace power magic mage witch wizard hunter killer slayer queen
prince princess crown throne empire shadow phantom eye eyes hand hands kid kids dog cat bird lion
tiger snake fox bear bunny crazy happy smile cute fake true real heart boss hero villain team
house home school city town street road way day days game play player level time over
mind body face voice song music dance party star little big great high low new old good bad
best last first one two three zero sin pain cry tears fall rise fly flying wings wing
ness less ful ing ers er ed ly ment
""".split())
_EN_MARK = re.compile(r"w|k|sh|th|ee|oo|ck|gh|ph|ou|ay|oy|aw|ew|tion$|y[^aeiou]|[^aeiou]y$", re.IGNORECASE)
_ES_SUFFIX = re.compile(r"(?:mente|ción|cion|dad|ando|iendo|aba|aban|ía|ían|ado|ada|ido|ida|oso|osa|ismo|ista)$",
                        re.IGNORECASE)
_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}")
_VOWELS = "aeiouáéíóúü"
_ACC = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú"}


def _split_en(w):
    """¿La palabra es una unión de palabras inglesas? (love+god)."""
    w = w.lower()
    n = len(w)
    best = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 2, min(n, i + 9) + 1):
            if w[i:j] in EN_WORDS and best[j] is None:
                best[j] = best[i] + [w[i:j]]
    return best[n]


def es_stress(word):
    """Fuerza la lectura en español: marca la sílaba tónica con tilde según las reglas del español."""
    if any(c in word for c in "áéíóúÁÉÍÓÚ"):
        return word
    nuclei = [m.span() for m in re.finditer(r"[aeiouAEIOU]+", word)]
    if not nuclei:
        return word
    last = word[-1].lower()
    idx = len(nuclei) - 2 if (last in "aeiouns" and len(nuclei) > 1) else len(nuclei) - 1
    a, b = nuclei[idx]
    grp = word[a:b]
    pos = None
    for strong in "aeoAEO":
        if strong in grp:
            pos = a + grp.index(strong)
            break
    if pos is None:
        pos = b - 1
    c = word[pos]
    acc = _ACC.get(c.lower(), c)
    acc = acc.upper() if c.isupper() else acc
    return word[:pos] + acc + word[pos + 1:]


_EN_RULES = [
    (r"tion\b", "shon"), (r"ay|ai", "ei"), (r"ight", "ait"), (r"ght", "t"), (r"ee|ea", "i"), (r"oo", "u"),
    (r"ou", "au"), (r"ow\b", "ou"), (r"ow", "au"), (r"oy", "oi"), (r"th", "d"), (r"ph", "f"), (r"ck", "k"), (r"wh", "u"), (r"w", "u"),
    (r"([^aeiou])y\b", r"\1i"), (r"\by", "y"), (r"([aeiou])([bcdfgklmnprstvz])e\b", r"\1\2"),
    (r"([^aeiou])e\b", r"\1"), (r"qu", "cu"), (r"x", "ks"), (r"j", "y"),
]


def _respell_part(w):
    if w in EN_PRON:
        return EN_PRON[w]
    for a, b in _EN_RULES:
        w = re.sub(a, b, w)
    return w


def en_respell(word):
    """Aproxima la pronunciación inglesa con letras del español (Shadow -> Shadau, Lovegod -> Lovgod)."""
    parts = _split_en(word)
    w = "".join(_respell_part(x) for x in parts) if parts and len(parts) >= 2 else _respell_part(word.lower())
    return (w[:1].upper() + w[1:]) if word[:1].isupper() else w


def detect_foreign(text, skip=()):
    """Lista de palabras que conviene revisar: [{word, count, parts, es, en}] por frecuencia."""
    lower_words = set(re.findall(r"\b[a-záéíóúñü]+\b", text))
    skip = {s.lower() for s in skip}
    counts = {}
    for m in _WORD.finditer(text):
        w = m.group(0)
        if w.lower() in skip:
            continue
        counts[w] = counts.get(w, 0) + 1
    out = []
    for w, c in counts.items():
        lw = w.lower()
        if w[0].islower() and lw in lower_words and lw not in EN_WORDS:
            continue
        if any(ch in "áéíóúñÁÉÍÓÚÑ" for ch in w) or _ES_SUFFIX.search(w):
            continue
        if lw in lower_words and lw not in EN_WORDS:
            continue
        parts = _split_en(w)
        marks = len(_EN_MARK.findall(w))
        if parts and (len(parts) >= 2 or (w[0].isupper() and lw in EN_WORDS and lw not in lower_words)):
            score = 3
        elif w[0].isupper() and len(w) >= 4 and marks >= 2 and lw not in lower_words:
            score = 2
        else:
            continue
        out.append({"word": w, "count": c, "parts": parts or [], "es": es_stress(w), "en": en_respell(w),
                    "score": score})
    out.sort(key=lambda d: (-d["score"], -d["count"], d["word"]))
    return out[:80]


def auto_pron(found, pron):
    """Elección por defecto: un nombre compuesto sin grafías inglesas (Lovegod) se lee como está escrito.
    Los que tienen gh, sh, w, th… (Nightwolf) se dejan a la voz hasta que el usuario elija."""
    for d in found:
        w = d["word"]
        if w not in pron and len(d.get("parts") or []) >= 2 and not _EN_MARK.search(w):
            pron[w] = "es"
    return pron


def pron_rules(pron):
    """pron: {palabra: 'es' | 'en' | 'raw' | texto propio} -> reglas de reemplazo."""
    out = []
    for w in sorted((pron or {}).keys(), key=lambda x: (-len(x), x)):
        mode = pron[w]
        if not w or mode in ("raw", "", None):
            continue
        rep = es_stress(w) if mode == "es" else en_respell(w) if mode == "en" else str(mode)
        out.append((re.compile(r"(?<!\w)" + re.escape(w) + r"(?!\w)"), rep))
    return out


def apply_pron(text, rules):
    for rx, rep in rules:
        text = rx.sub(lambda _m, r=rep: r, text)
    return text
