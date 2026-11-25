# -*- coding: utf-8 -*-
"""Subtítulos precisos para un video con la voz del usuario, sin internet.

1. Se saca el audio del video con FFmpeg (16 kHz, mono).
2. Vosk (reconocedor de voz local) da cada palabra con su inicio y su fin.
3. Si el usuario tiene el guion, se emparejan sus palabras con las oídas: los subtítulos llevan SU texto exacto
   y las palabras que el reconocedor oyó mal toman el tiempo de sus vecinas.
4. Los bordes de cada subtítulo se acomodan al silencio más cercano (la voz no empieza antes de que salga el texto).
5. Opcional: la IA del usuario solo revisa los tramos dudosos, con el contexto alrededor, y nunca toca los tiempos.
"""
import difflib
import io
import json
import os
import re
import subprocess
import unicodedata
import wave

import numpy as np

MODEL_NAME = "vosk-model-small-es-0.42"
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
LINE = 42             # caracteres por línea
MAX_CUE = 84          # dos líneas
MAX_DUR = 6.0         # segundos
MIN_DUR = 1.0
SOFT_CUE = 72         # un subtítulo más largo que esto se parte en su pausa más larga
SOFT_DUR = 5.0
GAP_BREAK = 0.45      # una pausa así de larga separa subtítulos
LOW_CONF = 0.6


# ----------------------------------------------------------------------------- audio
def extract_audio(video, wav, ffmpeg, cancel=None):
    """Audio del video a WAV 16 kHz mono (lo que necesita el reconocedor)."""
    cmd = [ffmpeg, "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    while p.poll() is None:
        if cancel is not None and cancel.wait(0.2):
            p.kill()
            raise RuntimeError("CANCELLED")
    if p.returncode != 0:
        raise RuntimeError("No se pudo leer el audio del video: " + p.stderr.read().decode("utf-8", "replace")[-300:])


def model_dir(base):
    return os.path.join(base, "modelos_voz", MODEL_NAME)


WHISPER_DIR = "whisper-small"
WHISPER_URL = "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/resolve/main/"
WHISPER_FILES = {"small-encoder.int8.onnx": 112442483, "small-decoder.int8.onnx": 262226114, "small-tokens.txt": 816730}


def whisper_dir(base):
    return os.path.join(base, "modelos_voz", WHISPER_DIR)


def have_vosk(base):
    return os.path.isfile(os.path.join(model_dir(base), "am", "final.mdl"))


def have_whisper(base):
    d = whisper_dir(base)
    return all(os.path.isfile(os.path.join(d, f)) and os.path.getsize(os.path.join(d, f)) == n
               for f, n in WHISPER_FILES.items())


def _download(url, path, progress=None, cancel=None):
    """Descarga a «path.part» y renombra al terminar: un corte no deja un archivo roto."""
    import urllib.request
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "AudioNovelaStudio"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            if cancel is not None and cancel.is_set():
                f.close()
                os.remove(tmp)
                raise RuntimeError("CANCELLED")
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            got += len(b)
            if progress and total:
                progress(got / total)
    os.replace(tmp, path)


def download_vosk(base, progress=None, cancel=None):
    """Modelo de español de Vosk (~40 MB), solo la primera vez."""
    import zipfile
    dst = os.path.join(base, "modelos_voz")
    os.makedirs(dst, exist_ok=True)
    z = os.path.join(dst, MODEL_NAME + ".zip")
    _download(MODEL_URL, z, progress, cancel)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(dst)
    os.remove(z)
    return model_dir(base)


def download_whisper(base, progress=None, cancel=None):
    """Whisper small (~375 MB): texto con puntuación y nombres propios; los tiempos los pone Vosk."""
    d = whisper_dir(base)
    os.makedirs(d, exist_ok=True)
    total = sum(WHISPER_FILES.values())
    done = 0
    for f, n in WHISPER_FILES.items():
        p = os.path.join(d, f)
        if not (os.path.isfile(p) and os.path.getsize(p) == n):
            _download(WHISPER_URL + f, p, (lambda x, d0=done, n=n: progress((d0 + x * n) / total)) if progress else None,
                      cancel)
        done += n
    return d


def whisper_text(wav, words, wdir, progress=None, cancel=None, threads=6):
    """Texto de Whisper por trozos de ~22 s cortados en silencios (Whisper no acepta más de 30 s)."""
    import sherpa_onnx as so
    rec = so.OfflineRecognizer.from_whisper(
        encoder=os.path.join(wdir, "small-encoder.int8.onnx"), decoder=os.path.join(wdir, "small-decoder.int8.onnx"),
        tokens=os.path.join(wdir, "small-tokens.txt"), language="es", task="transcribe", num_threads=threads)
    wf = wave.open(wav, "rb")
    sr = wf.getframerate()
    a = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    wf.close()
    cuts, start = [], 0.0
    for w0, w1 in zip(words, words[1:]):
        if w1["start"] - start > 22 and w1["start"] - w0["end"] > 0.15:
            cut = (w0["end"] + w1["start"]) / 2
            cuts.append((start, cut))
            start = cut
    cuts.append((start, len(a) / sr))
    parts = []
    for k, (s, e) in enumerate(cuts):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("CANCELLED")
        if e - s < 0.3:
            continue
        st = rec.create_stream()
        st.accept_waveform(sr, a[int(s * sr):int(e * sr)])
        rec.decode_stream(st)
        parts.append(st.result.text.strip())
        if progress:
            progress((k + 1) / len(cuts))
    return " ".join(p for p in parts if p)


def transcribe(wav, mdir, progress=None, cancel=None):
    """Palabras con tiempos: [{'word', 'start', 'end', 'conf'}]."""
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    wf = wave.open(wav, "rb")
    n = wf.getnframes()
    rec = KaldiRecognizer(Model(mdir), wf.getframerate())
    rec.SetWords(True)
    words, done = [], 0
    while True:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("CANCELLED")
        data = wf.readframes(8000)
        if not data:
            break
        done += len(data) // 2
        if rec.AcceptWaveform(data):
            words += json.loads(rec.Result()).get("result", [])
        if progress:
            progress(done / max(1, n))
    words += json.loads(rec.FinalResult()).get("result", [])
    wf.close()
    return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"]), "conf": float(w.get("conf", 1))}
            for w in words]


def energy(wav, hop=0.01):
    """Energía cada 10 ms (para encontrar los silencios)."""
    wf = wave.open(wav, "rb")
    sr = wf.getframerate()
    h = int(sr * hop)
    out, rest = [], np.zeros(0, np.float32)
    while True:                                  # por partes de ~1 min: un video de horas no llena la memoria
        raw = wf.readframes(sr * 60)
        if not raw:
            break
        a = np.concatenate([rest, np.frombuffer(raw, dtype=np.int16).astype(np.float32)])
        n = len(a) // h
        if n:
            out.append(np.sqrt((a[:n * h].reshape(n, h) ** 2).mean(axis=1)))
        rest = a[n * h:]
    wf.close()
    if not out:
        return np.zeros(1), hop
    return np.concatenate(out), hop


# ----------------------------------------------------------------------------- guion
_U = "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete " \
     "dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco veintiseis veintisiete " \
     "veintiocho veintinueve".split()
_D = "_ _ _ treinta cuarenta cincuenta sesenta setenta ochenta noventa".split()
_C = "_ ciento doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos".split()


def num_words(n):
    """Número en palabras («18» -> «dieciocho»), para comparar lo que escribió Whisper con lo que oyó Vosk."""
    if n < 30:
        return _U[n]
    if n < 100:
        return _D[n // 10] + ("" if n % 10 == 0 else " y " + _U[n % 10])
    if n == 100:
        return "cien"
    if n < 1000:
        return _C[n // 100] + ("" if n % 100 == 0 else " " + num_words(n % 100))
    if n < 10000:
        k = n // 1000
        return ("mil" if k == 1 else num_words(k) + " mil") + ("" if n % 1000 == 0 else " " + num_words(n % 1000))
    return str(n)


def _norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if unicodedata.category(c) != "Mn").replace("ñ", "ñ"))


def _norm_tokens(t):
    """Una palabra del guion puede valer varias al oírse: «18» = «dieciocho», «25» = «veinticinco», «31» = «treinta y uno»."""
    k = _norm(t)
    if k.isdigit() and len(k) <= 4:
        return [_norm(x) for x in num_words(int(k)).split()]
    return [k]


_TOKEN = re.compile(r"\S+")


def _opcodes(a, b, block=1200):
    """Como difflib.get_opcodes, pero por bloques: el tiempo crece en línea recta con el largo (antes, 50 000 palabras
    tardaban ~1 minuto y 500 000 habrían tardado horas). Cada bloque del guion se compara con una ventana de lo oído
    que avanza hasta la última coincidencia."""
    if len(a) <= block * 2:
        yield from difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        return
    i0 = j0 = 0
    while i0 < len(a):
        i1 = min(len(a), i0 + block)
        j1 = min(len(b), j0 + int((i1 - i0) * 1.35) + 250)
        ops = difflib.SequenceMatcher(None, a[i0:i1], b[j0:j1], autojunk=False).get_opcodes()
        if i1 < len(a):
            # el bloque se corta en su última coincidencia segura: lo que sigue se compara en el bloque siguiente
            k = max((n for n, o in enumerate(ops) if o[0] == "equal" and o[2] - o[1] >= 2), default=-1)
            if k >= 0 and ops[k][2] > (i1 - i0) // 3:
                ops = ops[:k + 1]
        for tag, x1, x2, y1, y2 in ops:
            yield tag, i0 + x1, i0 + x2, j0 + y1, j0 + y2
        ni, nj = ops[-1][2] if ops else i1 - i0, ops[-1][4] if ops else 0
        i0, j0 = i0 + max(1, ni), min(len(b), j0 + nj)


def align_script(script, words):
    """Tiempos para cada palabra del guion: [{'word' (con su puntuación), 'start', 'end', 'conf', 'matched'}]."""
    toks = [m.group(0) for m in _TOKEN.finditer(script)]
    a, owner = [], []                          # piezas comparables («18» -> «dieciocho»; «31» -> «treinta y uno»)
    for ti, t in enumerate(toks):
        for p in _norm_tokens(t):
            a.append(p)
            owner.append(ti)
    b = [_norm(w["word"]) for w in words]
    pt = [None] * len(a)                       # por pieza: (inicio, fin, confianza, coincide, otra versión)
    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        if tag == "equal":
            for k in range(i2 - i1):
                w = words[j1 + k]
                pt[i1 + k] = (w["start"], w["end"], w["conf"], True, None)
        elif tag == "replace" and j2 > j1:
            # tramo oído distinto: se reparte su tiempo entre las piezas, por largo
            t0, t1 = words[j1]["start"], words[j2 - 1]["end"]
            alt = " ".join(w["word"] for w in words[j1:j2])
            lens = [max(1, len(a[i])) for i in range(i1, i2)]
            tot, acc = sum(lens), 0
            for k, i in enumerate(range(i1, i2)):
                s = t0 + (t1 - t0) * acc / tot
                acc += lens[k]
                pt[i] = (s, t0 + (t1 - t0) * acc / tot, 0.3, False, alt)
    out = [{"word": t, "start": None, "end": None, "conf": 0.0, "matched": False, "heard": False} for t in toks]
    groups = {}
    for i, ti in enumerate(owner):
        groups.setdefault(ti, []).append(pt[i])
    for ti, ps in groups.items():
        got = [p for p in ps if p is not None]
        if not got:
            continue
        o = out[ti]
        o.update(start=min(p[0] for p in got), end=max(p[1] for p in got), conf=min(p[2] for p in got),
                 matched=len(got) == len(ps) and all(p[3] for p in got), heard=True)
        alts = [p[4] for p in got if p[4]]
        if alts:
            o["alt"] = " / ".join(dict.fromkeys(alts))
    # palabras que no se oyeron: entre sus vecinas con tiempo
    i = 0
    while i < len(out):
        if out[i]["start"] is None:
            j = i
            while j < len(out) and out[j]["start"] is None:
                j += 1
            t0 = out[i - 1]["end"] if i > 0 else (out[j]["start"] if j < len(out) else 0.0)
            t1 = out[j]["start"] if j < len(out) else t0 + 0.3 * (j - i)
            for k in range(i, j):
                out[k].update(start=t0 + (t1 - t0) * (k - i) / (j - i), end=t0 + (t1 - t0) * (k - i + 1) / (j - i))
            i = j
        else:
            i += 1
    matched = sum(1 for o in out if o["matched"])
    return out, matched / max(1, len(out))


# ----------------------------------------------------------------------------- subtítulos
def _text(ws):
    return " ".join(w["word"] for w in ws)


def build_cues(words):
    """Agrupa palabras en subtítulos: cortes en puntuación y pausas, hasta 2 líneas y 6 s."""
    cues, cur = [], []
    for k, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            long_ = len(_text(cur + [w])) > MAX_CUE or w["end"] - cur[0]["start"] > MAX_DUR
            punct = re.search(r"[.!?…:;]$", cur[-1]["word"]) is not None
            comma = cur[-1]["word"].endswith(",") and len(_text(cur)) > LINE * 0.8
            if long_ or gap > GAP_BREAK or punct or comma:
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)

    def split(ws):            # muy largo: se parte en la pausa más larga, prefiriendo el medio
        if len(ws) < 4 or (len(_text(ws)) <= SOFT_CUE and ws[-1]["end"] - ws[0]["start"] <= SOFT_DUR):
            return [ws]
        best, bk = -1.0, None
        for k in range(2, len(ws) - 1):
            gap = ws[k]["start"] - ws[k - 1]["end"]
            mid = 1 - abs(len(_text(ws[:k])) / max(1, len(_text(ws))) - 0.5)
            score = gap + 0.08 * mid + (0.3 if re.search(r"[,;:]$", ws[k - 1]["word"]) else 0)
            if score > best:
                best, bk = score, k
        return split(ws[:bk]) + split(ws[bk:])
    cues = [p for ws in cues for p in split(ws)]
    # muy cortos («Número 1.»): se unen al siguiente si juntos siguen siendo legibles
    merged = []
    for ws in cues:
        if merged:
            prev = merged[-1]
            short = prev[-1]["end"] - prev[0]["start"] < 1.1 or ws[-1]["end"] - ws[0]["start"] < 0.8
            close = ws[0]["start"] - prev[-1]["end"] < 0.6
            if short and close and len(_text(prev + ws)) <= SOFT_CUE and ws[-1]["end"] - prev[0]["start"] <= SOFT_DUR:
                merged[-1] = prev + ws
                continue
        merged.append(ws)
    cues = merged
    out = []
    for ws in cues:
        out.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": _text(ws),
                    "doubt": any(w.get("conf", 1) < LOW_CONF or w.get("ai") for w in ws), "words": ws})
    return out


def snap(cues, en, hop, look=0.25):
    """Inicio justo antes de que suene la voz y fin justo después; sin pisarse; mínimo 1 s en pantalla."""
    if len(en) < 2:
        return cues
    thr = max(float(np.percentile(en, 20)) * 2.2, float(en.max()) * 0.04)
    n = len(en)
    for c in cues:
        i = int(c["start"] / hop)
        lo = max(0, i - int(look / hop))
        k = i
        while k > lo and en[k - 1] > thr:        # retrocede mientras haya voz
            k -= 1
        c["start"] = max(0.0, k * hop - 0.04)
        j = min(n - 1, int(c["end"] / hop))
        hi = min(n - 1, j + int(look / hop))
        while j < hi and en[j + 1] > thr:        # avanza mientras siga la voz
            j += 1
        c["end"] = j * hop + 0.08
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"] - 0.04:
            a["end"] = max(a["start"] + 0.3, b["start"] - 0.04)
        if a["end"] - a["start"] < MIN_DUR:
            a["end"] = min(a["start"] + MIN_DUR, b["start"] - 0.04)
    if cues and cues[-1]["end"] - cues[-1]["start"] < MIN_DUR:
        cues[-1]["end"] = cues[-1]["start"] + MIN_DUR
    return cues


def wrap(t):
    if len(t) <= LINE:
        return t
    mid = len(t) // 2
    cut = min((m.start() for m in re.finditer(" ", t)), key=lambda p: abs(p - mid), default=-1)
    return t if cut < 0 else t[:cut] + "\n" + t[cut + 1:]


def _ts(s, sep=","):
    s = max(0.0, s)
    h, m, x = int(s // 3600), int(s % 3600 // 60), s % 60
    return f"{h:02d}:{m:02d}:{int(x):02d}{sep}{int(round((x - int(x)) * 1000)) % 1000:03d}"


def write_srt(cues, path):
    with io.open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{wrap(c['text'])}\n\n")


def write_vtt(cues, path):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for c in cues:
            f.write(f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{wrap(c['text'])}\n\n")


def capitalize(cues):
    """Texto oído (sin guion): mayúscula al empezar una frase."""
    first = True
    for c in cues:
        t = c["text"]
        if first and t:
            c["text"] = t[0].upper() + t[1:]
        first = bool(re.search(r"[.!?…]$", c["text"]))
    return cues


# ----------------------------------------------------------------------------- IA solo para los tramos dudosos
SYSTEM = ("Corriges transcripciones automáticas de voz en español. Solo cambias las palabras marcadas como dudosas "
          "cuando el contexto lo deja claro (nombres propios, títulos de anime o películas, palabras mal oídas). "
          "Nunca inventas contenido nuevo. Si no estás seguro, dejas la palabra como está. Respondes solo con JSON.")


def doubtful_spans(words, ctx=12, maxspan=8):
    """Tramos con palabras dudosas: (i0, i1) en índices de palabras."""
    spans, i = [], 0
    while i < len(words):
        if words[i]["conf"] < LOW_CONF:
            j = i
            while j + 1 < len(words) and (words[j + 1]["conf"] < LOW_CONF or
                                          (j + 2 < len(words) and words[j + 2]["conf"] < LOW_CONF)) and j - i < maxspan:
                j += 1
            spans.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return spans


def ai_prompt(words, spans, topic=""):
    """Un mensaje con varios tramos: cada uno con su contexto; la IA devuelve {id: "texto corregido"}."""
    items = []
    for n, (a, b) in enumerate(spans, 1):
        before = " ".join(w["word"] for w in words[max(0, a - 14):a])
        after = " ".join(w["word"] for w in words[b:b + 14])
        mid = " ".join(w["word"] for w in words[a:b])
        items.append(f"[{n}] …{before} <<{mid}>> {after}…")
    tema = f"Tema del video: {topic}\n" if topic else ""
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": tema + "En cada tramo, lo que está entre << >> se oyó con dudas. Escribe cómo "
             "debería decir SOLO esa parte (mismo largo aproximado). Si no estás seguro, repítela igual.\n\n"
             + "\n".join(items) + '\n\nDevuelve solo JSON: {"1": "texto", "2": "texto"}'}]


def ai_apply(words, spans, reply):
    """Aplica las correcciones sin tocar los tiempos: el tramo nuevo reparte el tiempo del viejo."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return words, 0
    try:
        fix = json.loads(m.group(0))
    except ValueError:
        return words, 0
    out, last, changed = [], 0, 0
    for n, (a, b) in enumerate(spans, 1):
        out += words[last:a]
        new = str(fix.get(str(n), "")).strip()
        old = " ".join(w["word"] for w in words[a:b])
        toks = new.split()
        if not toks or new.lower() == old.lower() or len(toks) > 3 * (b - a) + 2:   # sin cambio o sospechoso
            out += words[a:b]
        else:
            t0, t1 = words[a]["start"], words[b - 1]["end"]
            for k, t in enumerate(toks):
                out.append({"word": t, "start": t0 + (t1 - t0) * k / len(toks),
                            "end": t0 + (t1 - t0) * (k + 1) / len(toks), "conf": 0.59, "ai": True})
            changed += 1
        last = b
    out += words[last:]
    return out, changed


# ----------------------------------------------------------------------------- texto de Whisper + tiempos de Vosk
def drop_unheard(al, min_run=4):
    """Whisper a veces inventa frases en los silencios: un tramo de 4 o más palabras sin ningún sonido detrás se quita."""
    out, run = [], []
    for w in al + [None]:
        if w is not None and not w["heard"]:
            run.append(w)
            continue
        if run and len(run) < min_run:
            out += run
        run = []
        if w is not None:
            out.append(w)
    return out, len(al) - len(out)


def compare_spans(al, maxspan=10):
    """Tramos donde Whisper y Vosk no coinciden: [(i0, i1)] sobre las palabras alineadas."""
    spans, i = [], 0
    while i < len(al):
        if not al[i]["matched"] and not al[i].get("ai"):
            j = i
            while j + 1 < len(al) and not al[j + 1]["matched"] and not al[j + 1].get("ai") and j - i < maxspan:
                j += 1
            spans.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return spans


MAX_AI_SPANS = 250          # tope de dudas que se mandan a la IA (10 consultas)
_STOP = set("a al ante con de del el en es la las le les lo los me mi no o para pero por que se si sin su sus te tu "
            "un una uno unos y ya yo así asi donde cuando como muy más mas ese esa eso este esta esto hay fue era".split())


def _important(al, span):
    """¿Vale la pena preguntarle a la IA? Sí si hay un nombre propio o una palabra de verdad (no «y», «a», «la»)."""
    a, b = span
    ws = [re.sub(r"[^\wáéíóúñü]", "", w["word"]) for w in al[a:b]]
    for k, (w, raw) in enumerate(zip(ws, al[a:b])):
        if not w:
            continue
        prev = al[a + k - 1]["word"] if a + k > 0 else "."
        if w[:1].isupper() and not re.search(r"[.!?¿¡]$", prev):     # mayúscula a mitad de frase: nombre propio
            return True
        if len(w) >= 5 and w.lower() not in _STOP:
            return True
    return len([w for w in ws if w and w.lower() not in _STOP]) >= 2


def ai_prompt_compare(al, spans, topic=""):
    """Para cada duda: lo que escribió Whisper, lo que oyó Vosk y el contexto. La IA solo elige o completa el tramo."""
    items = []
    for n, (a, b) in enumerate(spans, 1):
        before = " ".join(w["word"] for w in al[max(0, a - 16):a])
        after = " ".join(w["word"] for w in al[b:b + 16])
        wtxt = " ".join(w["word"] for w in al[a:b])
        vtxt = " / ".join(dict.fromkeys(w.get("alt", "") for w in al[a:b] if w.get("alt"))) or "(nada claro)"
        items.append(f"[{n}] ...{before} <<{wtxt}>> {after}...\n    otro reconocedor oyó: «{vtxt}»")
    tema = f"Tema del video: {topic}\n" if topic else ""
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": tema + "Dos reconocedores de voz no coincidieron en los tramos entre << >>. "
             "Con el contexto, escribe cómo debe decir SOLO ese tramo (nombres propios bien escritos, mismas palabras "
             "si ya está bien). No agregues nada que no se haya dicho. Si dudas, repítelo igual.\n\n"
             + "\n".join(items) + '\n\nDevuelve solo JSON: {"1": "texto", "2": "texto"}'}]


def _sim(x, y):
    """Parecido entre dos textos (0..1), sin tildes ni signos."""
    a = " ".join(_norm(t) for t in x.split())
    b = " ".join(_norm(t) for t in y.split())
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def ai_apply_aligned(al, spans, reply):
    """Cambia el texto de los tramos dudosos repartiendo su tiempo; el resto queda intacto."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return al, 0
    try:
        fix = json.loads(m.group(0))
    except ValueError:
        return al, 0
    out, last, changed = [], 0, 0
    for n, (a, b) in enumerate(spans, 1):
        out += al[last:a]
        new = str(fix.get(str(n), "")).strip()
        old = " ".join(w["word"] for w in al[a:b])
        toks = new.split()
        # las IA pequeñas a veces repiten la palabra vecina («Keita Keita»): se quita lo que ya está al lado
        while toks and a > 0 and _norm(toks[0]) and _norm(toks[0]) == _norm(al[a - 1]["word"]) and _norm(toks[0]) != _norm(al[a]["word"]):
            toks = toks[1:]
        while toks and b < len(al) and _norm(toks[-1]) and _norm(toks[-1]) == _norm(al[b]["word"]) and _norm(toks[-1]) != _norm(al[b - 1]["word"]):
            toks = toks[:-1]
        new = " ".join(toks)
        alt = " ".join(dict.fromkeys(w.get("alt", "") for w in al[a:b] if w.get("alt")))
        heard_like = _sim(new, old)          # solo se acepta corregir la escritura de lo que ya había (p. ej. un nombre)
        if alt and _sim(new, alt) > heard_like:
            heard_like = 0.0                 # copió lo que oyó mal el otro reconocedor: se descarta
        if (not toks or new == old or _sim(new, old) >= 0.999 or len(toks) > 2 * (b - a) + 3 or "nada claro" in new.lower()
                or heard_like < 0.6):                        # cambia demasiado: se descarta (la IA no inventa)
            out += [dict(w, ai=True) for w in al[a:b]]          # revisado sin cambio: no se vuelve a preguntar
        else:
            t0, t1 = al[a]["start"], al[b - 1]["end"]
            for k, t in enumerate(toks):
                out.append({"word": t, "start": t0 + (t1 - t0) * k / len(toks), "end": t0 + (t1 - t0) * (k + 1) / len(toks),
                            "conf": 0.5, "matched": False, "heard": True, "ai": True, "ai_changed": True})
            changed += 1
        last = b
    out += al[last:]
    return out, changed


def run(video, base, ffmpeg, script="", precise=True, ai_chat=None, topic="", progress=None, cancel=None, workdir=None,
        fast=False, gpu=False, hotwords=""):
    """Todo el proceso. progress(fase, fracción). ai_chat(messages) -> texto (opcional). Devuelve (cues, info).
    fast: usar faster-whisper (texto, puntuación y tiempos en un paso); gpu: en la tarjeta NVIDIA."""
    import tempfile
    say = progress or (lambda *_: None)
    tmpd = workdir or tempfile.mkdtemp(prefix="ans_subs_")
    wav = os.path.join(tmpd, "voz.wav")
    say("Leyendo el audio del video…", 0.0)
    extract_audio(video, wav, ffmpeg, cancel)
    info = {}
    if precise and not script.strip() and fast and fw_installed():
        try:
            name = "turbo" if gpu else "small"
            if gpu and not have_cuda(base):
                msg = "Bajando la librería de NVIDIA (553 MB, solo la primera vez)…"
                say(msg, 0.0)
                download_cuda(base, lambda f: say(msg, f), cancel)
            if not have_fw(base, name):
                msg = f"Bajando Whisper {'turbo (1,6 GB)' if gpu else 'small (480 MB)'}, solo la primera vez…"
                say(msg, 0.0)
                download_fw(base, name, lambda f: say(msg, f), cancel)
            msg = f"Escribiendo el texto con tiempos ({'tarjeta NVIDIA' if gpu else 'procesador'}, sin internet)…"
            say(msg, 0.0)
            fw_words, finfo = fw_transcribe(wav, base, gpu, lambda f: say(msg, f), cancel, tmpd, hotwords)
            if not fw_words:
                raise RuntimeError("No se oyó voz en el video.")
            al = [dict(w, matched=w["conf"] >= 0.5, heard=True) for w in fw_words]
            info.update(mode="whisper", engine=("turbo/GPU" if gpu else "small/CPU"), asr_seconds=finfo["seconds"],
                        match=sum(w["matched"] for w in al) / max(1, len(al)))
            return _finish(al, info, wav, ai_chat, topic, say, cancel)
        except RuntimeError as e:
            if str(e) == "CANCELLED":
                raise
            info["fallback"] = str(e)[:300]          # se sigue con el motor de respaldo (Vosk + Whisper)
    if not have_vosk(base):
        msg = "Bajando el reconocedor de voz (40 MB, solo la primera vez)…"
        say(msg, 0.0)
        download_vosk(base, lambda f: say(msg, f), cancel)
    msg = "Escuchando el audio (tiempo de cada palabra)…"
    words = transcribe(wav, model_dir(base), lambda f: say(msg, f), cancel)
    if not words:
        raise RuntimeError("No se oyó voz en el video.")
    info["vosk_words"] = len(words)
    if script.strip():
        al, rate = align_script(script, words)
        info.update(mode="guion", match=rate)
    elif precise:
        if not have_whisper(base):
            msg = "Bajando Whisper (375 MB, solo la primera vez)…"
            say(msg, 0.0)
            download_whisper(base, lambda f: say(msg, f), cancel)
        msg = "Escribiendo el texto con puntuación (Whisper, sin internet)…"
        text = whisper_text(wav, words, whisper_dir(base), lambda f: say(msg, f), cancel)
        al, rate = align_script(text, words)
        al, dropped = drop_unheard(al)
        info.update(mode="whisper", match=rate, dropped=dropped)
    else:
        al = [dict(w, matched=w["conf"] >= LOW_CONF, heard=True) for w in words]
        info.update(mode="rapido", match=sum(w["matched"] for w in al) / max(1, len(al)))
    return _finish(al, info, wav, ai_chat, topic, say, cancel)


def _finish(al, info, wav, ai_chat, topic, say, cancel=None):
    """Revisión opcional de dudas con IA, armado de subtítulos y ajuste a los silencios."""
    spans = compare_spans(al)
    info["doubts"] = len(spans)
    info["ai_fixed"] = 0
    if ai_chat and spans:
        # solo las dudas que importan (nombres, palabras largas), las más dudosas primero y con tope: antes un video
        # largo mandaba miles de consultas («y», «a», «la»…) y la revisión parecía trabada durante horas
        imp = [s for s in spans if _important(al, s)]
        imp.sort(key=lambda s: min(w.get("conf", 0.5) for w in al[s[0]:s[1]]))
        chosen = sorted(imp[:MAX_AI_SPANS], key=lambda s: s[0], reverse=True)
        info["ai_sent"], info["ai_skipped"] = len(chosen), len(spans) - len(chosen)
        done = 0
        for k in range(0, len(chosen), 25):            # de atrás hacia adelante: los índices de lo que falta no se mueven
            if cancel is not None and cancel.is_set():
                raise RuntimeError("CANCELLED")
            part = sorted(chosen[k:k + 25])
            say(f"Tu IA revisa las dudas importantes ({done}/{len(chosen)})…", done / max(1, len(chosen)))
            try:
                reply = ai_chat(ai_prompt_compare(al, part, topic))
            except Exception as e:
                info["ai_error"] = str(e)[:200]
                break
            al, n = ai_apply_aligned(al, part, reply)
            info["ai_fixed"] += n
            done += len(part)
    for w in al:
        if not w.get("matched"):
            w["conf"] = min(w.get("conf", 0.5), 0.5)
    say("Armando los subtítulos…", 1.0)
    en, hop = energy(wav)
    cues = snap(build_cues(al), en, hop)
    if info["mode"] == "rapido":
        capitalize(cues)
    info["wav"] = wav
    return cues, info


def write_ass(cues, path, font="Segoe UI", size=54, color="&H00FFFFFF", outline="&H00000000", pos="abajo"):
    align = {"abajo": 2, "medio": 5, "arriba": 8}.get(pos, 2)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Default,{font},{size},{color},&H000000FF,{outline},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,{align},"
            "80,80,70,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def ts(s):
        s = max(0.0, s)
        return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for c in cues:
            t = wrap(c["text"]).replace("\n", "\\N").replace("{", "(").replace("}", ")")
            f.write(f"Dialogue: 0,{ts(c['start'])},{ts(c['end'])},Default,,0,0,0,,{t}\n")


def burn(video, ass, out, ffmpeg, progress=None, cancel=None, total=0.0):
    """Graba los subtítulos dentro del video (NVIDIA si hay, si no el procesador). El audio se copia tal cual."""
    def esc(p):
        return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    part = out + ".part.mp4"
    for enc in (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]):
        cmd = [ffmpeg, "-hide_banner", "-y", "-nostdin", "-i", video, "-vf", f"ass='{esc(ass)}'", *enc,
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-map", "0:v:0", "-map", "0:a:0?", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", part]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            if cancel is not None and cancel.is_set():
                p.kill()
                break
            m = re.match(r"out_time_(?:ms|us)=(\d+)", line)
            if m and progress and total:
                progress(min(1.0, int(m.group(1)) / 1e6 / total))
        p.wait()
        if cancel is not None and cancel.is_set():
            try:
                os.remove(part)
            except OSError:
                pass
            raise RuntimeError("CANCELLED")
        if p.returncode == 0 and os.path.isfile(part):
            os.replace(part, out)
            return out
    raise RuntimeError("FFmpeg no pudo grabar los subtítulos en el video.")


# ============================================================================ motor rápido: faster-whisper
# Con tarjeta NVIDIA: «large-v3-turbo» en la tarjeta (~25 veces más rápido que el tiempo real).
# Sin tarjeta: «small» en el procesador (~2,6 veces). Ambos dan texto con puntuación y el tiempo de cada palabra.
FW_MODELS = {
    "turbo": ("https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/",
              {"config.json": 2263, "model.bin": 1617884929, "preprocessor_config.json": 340, "tokenizer.json": 2710337,
               "vocabulary.json": 1068114}),
    "small": ("https://huggingface.co/Systran/faster-whisper-small/resolve/main/",
              {"config.json": 2370, "model.bin": 483546902, "tokenizer.json": 2203239, "vocabulary.txt": 459861}),
}
CUBLAS_WHEEL = ("https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
                "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl", 553162896)
CUBLAS_DLLS = {"cublas64_12.dll": 102518272, "cublasLt64_12.dll": 668673536}


def fw_installed():
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec("ctranslate2") is not None


def has_nvidia():
    """¿Hay una tarjeta NVIDIA que usar? (sin cargar nada de CUDA en la app; respeta «device»: cpu)."""
    try:
        import hardware
        return hardware.use_cuda()
    except Exception:
        pass
    import shutil
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and "GPU" in r.stdout
    except Exception:
        return False


def fw_dir(base, name):
    return os.path.join(base, "modelos_voz", f"faster-whisper-{name}")


def cuda_dir(base):
    return os.path.join(base, "modelos_voz", "cuda")


def have_fw(base, name):
    d = fw_dir(base, name)
    return all(os.path.isfile(os.path.join(d, f)) and os.path.getsize(os.path.join(d, f)) == n
               for f, n in FW_MODELS[name][1].items())


def have_cuda(base):
    d = cuda_dir(base)
    return all(os.path.isfile(os.path.join(d, f)) and os.path.getsize(os.path.join(d, f)) == n for f, n in CUBLAS_DLLS.items())


def download_fw(base, name, progress=None, cancel=None):
    url, files = FW_MODELS[name]
    d = fw_dir(base, name)
    os.makedirs(d, exist_ok=True)
    total, done = sum(files.values()), 0
    for f, n in files.items():
        p = os.path.join(d, f)
        if not (os.path.isfile(p) and os.path.getsize(p) == n):
            _download(url + f, p, (lambda x, d0=done, n=n: progress((d0 + x * n) / total)) if progress else None, cancel)
        done += n
    return d


def download_cuda(base, progress=None, cancel=None):
    """Solo las 2 librerías de NVIDIA que hacen falta (cuBLAS), sacadas del paquete oficial (553 MB, una vez)."""
    import zipfile
    d = cuda_dir(base)
    os.makedirs(d, exist_ok=True)
    whl = os.path.join(d, "cublas.whl")
    _download(CUBLAS_WHEEL[0], whl, progress, cancel)
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            fn = os.path.basename(info.filename)
            if fn in CUBLAS_DLLS:
                with z.open(info) as src, open(os.path.join(d, fn + ".part"), "wb") as dst:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        dst.write(b)
                os.replace(os.path.join(d, fn + ".part"), os.path.join(d, fn))
    os.remove(whl)
    return d


def worker_cmd(job_path):
    """El mismo programa en modo «--asr» (compilado) o python app.py --asr (desde el código)."""
    import sys
    if getattr(sys, "frozen", False):
        return [sys.executable, "--asr", job_path]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), "--asr", job_path]


def fw_transcribe(wav, base, gpu, progress=None, cancel=None, tmpd=None, hotwords=""):
    """Lanza el trabajador y espera su resultado. Devuelve (palabras, info) o lanza RuntimeError."""
    import tempfile
    import time
    tmpd = tmpd or tempfile.mkdtemp(prefix="ans_asr_")
    name = "turbo" if gpu else "small"
    job = {"wav": wav, "model_dir": fw_dir(base, name), "device": "cuda" if gpu else "cpu",
           "compute": "float16" if gpu else "int8", "cuda_dir": cuda_dir(base),
           "out": os.path.join(tmpd, "asr_out.json"), "progress": os.path.join(tmpd, "asr_prog.txt"),
           "hotwords": (hotwords or "")[:600]}         # nombres propios que Whisper debe esperar (de AniList)
    jp = os.path.join(tmpd, "asr_job.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(job, f)
    for p in (job["out"], job["progress"]):
        if os.path.exists(p):
            os.remove(p)
    err = os.path.join(tmpd, "asr_err.txt")
    with open(err, "w", encoding="utf-8") as ef:
        p = subprocess.Popen(worker_cmd(jp), stdout=subprocess.DEVNULL, stderr=ef,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while p.poll() is None:
            if cancel is not None and cancel.is_set():
                p.kill()
                raise RuntimeError("CANCELLED")
            try:
                with open(job["progress"], encoding="utf-8") as pf:
                    if progress:
                        progress(float(pf.read() or 0))
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
    if p.returncode != 0 or not os.path.isfile(job["out"]):
        try:
            tail = open(err, encoding="utf-8", errors="replace").read()[-600:]
        except OSError:
            tail = ""
        raise RuntimeError(f"el motor rápido se detuvo (código {p.returncode}). {tail}")
    with open(job["out"], encoding="utf-8") as f:
        res = json.load(f)
    return res["words"], {"seconds": res.get("seconds", 0), "device": res.get("device")}


# ----------------------------------------------------------------------------- Ollama (IA local, sin internet)
def ollama_models(url="http://127.0.0.1:11434", timeout=1.5):
    """Modelos de Ollama instalados en esta PC ([] si no está abierto)."""
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=timeout) as r:
            ms = json.load(r).get("models", [])
        # los que caben en la tarjeta (más livianos) primero; se omiten los de solo embeddings
        ms = [m for m in ms if "embed" not in m["name"]]
        return [m["name"] for m in sorted(ms, key=lambda m: m.get("size", 0))]
    except Exception:
        return []


def ollama_chat_fn(model, url="http://127.0.0.1:11434"):
    """Función de chat para la revisión de dudas con Ollama (qwen3 «piensa» si no se le pide lo contrario)."""
    import urllib.request

    def chat(msgs):
        msgs = [dict(m) for m in msgs]
        if "qwen3" in model and msgs:
            msgs[-1]["content"] += " /no_think"
        body = json.dumps({"model": model, "messages": msgs, "stream": False, "think": False,
                           "options": {"temperature": 0.1, "num_ctx": 8192}, "keep_alive": "10m"}).encode("utf-8")
        req = urllib.request.Request(url + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            txt = json.load(r).get("message", {}).get("content", "")
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
    return chat
