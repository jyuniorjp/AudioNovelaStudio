# -*- coding: utf-8 -*-
"""
Motor de audio.
1. Convierte los segmentos analizados en fragmentos de voz (voz, velocidad, tono, volumen).
2. Los sintetiza en paralelo con las voces neurales de Microsoft.
3. Los une de forma natural: recorta silencios del servicio, iguala el volumen entre voces,
   suaviza los bordes y coloca pausas según el texto (inciso, párrafo, escena, capítulo).
4. Mezcla música de fondo opcional que baja sola cuando alguien habla.
5. Escribe MP3 o WAV, subtítulos .srt y marcas de tiempo por capítulo.
"""
import asyncio
import io
import math
import os
import re
import socket
import sys
import threading
import time
import wave
from dataclasses import dataclass, field

import numpy as np
import edge_tts

import brain
import estudio
import hardware
from docs import clean_for_speech, parse_lexicon, apply_lexicon, pron_rules, apply_pron

SR_TTS = 24000


@dataclass
class Settings:
    narrator: str = "es-MX-JorgeNeural"
    speed: float = 1.75
    pitch: int = -8
    volume: int = 0
    character_voices: bool = True
    scene_moods: bool = True
    emotions: bool = True
    emotion_intensity: float = 1.0
    pron: dict = field(default_factory=dict)     # palabra -> cómo leerla (detector de pronunciación)
    emo_over: dict = field(default_factory=dict)  # clave del diálogo -> emoción elegida a mano
    skip_tags: bool = False
    skip_notes: bool = True      # no leer notas del autor (N/A, Nota del autor…) — igual en brain.js
    skip_brackets: bool = False
    math_mode: str = "auto"      # fórmulas en español: auto (frase por frase), on (siempre), off (nunca)
    skip_citations: bool = True  # saltar [12], (Autor, 2020) y la sección «Referencias»  # no leer texto entre [corchetes] (ojo: en fanfics de «sistema» son parte de la historia)
    read_titles: bool = True
    generic_male: str = "es-US-AlonsoNeural"
    generic_female: str = "es-MX-DaliaNeural"
    cast: dict = field(default_factory=dict)
    lexicon: str = ""
    pause_inner: int = 160
    pause_para: int = 420
    pause_title: int = 1100
    pause_scene: int = 1500
    music_path: str = ""
    music_db: float = -24.0
    duck: bool = True
    out_rate: int = 44100
    bitrate: int = 128
    voice_mode: str = "auto"     # auto | online | offline (igual que «Fuente de voces» de la APK)
    on_drop: str = "wait"        # si se cae internet a mitad: wait (esperar) | switch (seguir con la voz natural)


@dataclass
class Chunk:
    text: str
    voice: str
    rate: int
    pitch: int
    gain: float          # multiplicador de volumen local (1.0 = normal)
    pause_ms: int
    src_start: int
    src_end: int
    who: str = ""        # nombre del personaje o '' si narrador
    chapter: int = 0
    emo: str = ""        # emoción con la que se lee
    hard: bool = False   # lleva una pausa dramática: no se une con el siguiente


MAX_CHARS = 1500


def _split_long(text, limit=MAX_CHARS):
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?…»”])\s+", text):
        while len(sent) > limit:
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:cut])
            sent = sent[cut:].lstrip()
        if len(cur) + len(sent) + 1 > limit:
            if cur:
                out.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        out.append(cur)
    return out


def _round_half(x):
    """Redondeo idéntico en Python y JavaScript (mitades hacia arriba)."""
    return int(math.floor(x + 0.5))


# Notas del autor al inicio de un párrafo: «N/A:», «A/N», «Nota del autor:», «Notas finales», «Author's note»…
NOTE_RE = re.compile(r"^\s*[\(\[*_]*\s*(?:N\s*/\s*A\b|A\s*/\s*N\b|N\.\s?A\.|Notas?\s+(?:de(?:l)?\s+)?(?:la\s+)?"
                     r"(?:autor(?:a)?|escritor(?:a)?)\b|Notas?\s+finales\b|Author'?s?\s+notes?\b)", re.I)
BRACKET_RE = re.compile(r"\[[^\]\n]{0,400}\]")


def build_chunks(segs, st, start=0, end=None, chapter=0):
    end = 10 ** 12 if end is None else end
    lex = parse_lexicon(st.lexicon)
    pron = pron_rules(st.pron)
    base_rate = _round_half((st.speed - 1.0) * 100)
    pauses = {"inner": st.pause_inner, "para": st.pause_para,
              "title": st.pause_title, "scene": st.pause_scene}
    sp = max(0.6, st.speed) ** 0.5
    k = max(0.0, min(1.5, st.emotion_intensity))
    kp = min(k, 1.0)
    out = []
    in_refs = False
    for s in segs:
        if s.start < start or s.start >= end:
            continue
        raw = s.text
        if st.skip_citations:
            if s.kind != "dlg" and estudio.REF_HEAD.match(raw):
                in_refs = True               # desde «Referencias» hasta el final del capítulo no se lee
            if in_refs:
                continue
            raw = estudio.strip_citations(raw)
        if s.kind == "title" and not st.read_titles:
            continue
        raw = estudio.math_speech(raw, st.math_mode)   # antes de limpiar: la limpieza borra *, _ y >
        t = clean_for_speech(raw)
        if st.skip_notes and s.kind == "narr" and NOTE_RE.match(s.text):
            continue
        if st.skip_brackets:
            t = BRACKET_RE.sub(" ", t).strip()
        if not re.search(r"\w", t):
            continue
        if st.skip_tags and s.kind == "narr" and len(t) < 70 and brain.RE_SPEECH.match(t):
            if out:
                out[-1].pause_ms = max(out[-1].pause_ms, int(pauses.get(s.pause, 0) / sp))
            continue
        voice, pitch0, rate0, vol0, who = st.narrator, st.pitch, base_rate, st.volume, ""
        if s.kind == "dlg" and st.character_voices:
            c = st.cast.get(s.speaker) if s.speaker else None
            if c and not c.get("muted"):
                voice, pitch0, who = c["voice"], int(c.get("pitch", 0)), s.speaker
                rate0 += int(c.get("rate", 0))
            elif s.hint == "F":
                voice, pitch0 = st.generic_female, 0
            elif s.hint == "M":
                voice, pitch0 = st.generic_male, -3
        sents = [x for x in brain.SENT_SPLIT.split(t) if re.search(r"\w", x)] if s.kind != "title" else [t]
        seg_pause = int(pauses.get(s.pause, st.pause_inner) / sp)
        for si, sent in enumerate(sents):
            rate, pitch, vol, extra, emo = rate0, pitch0, vol0, 0, ""
            lock = st.emo_over.get(s.key) if (st.emo_over and s.key) else None
            if lock in brain.EMOTIONS:
                # emoción elegida a mano para esta línea
                emo, kk = lock, max(k, 1.0)
                dr, dp, dv = brain.EMOTIONS[lock]
                rate += _round_half(dr * kk)
                pitch += _round_half(dp * min(kk, 1.0))
                vol += _round_half(dv * kk)
                if sent.rstrip("»”\"' ").endswith(("...", "…")):
                    extra = 300
            elif s.kind == "dlg" and st.emotions and k > 0:
                emo = brain.sentence_emotion(sent, s.emotion)
                dr, dp, dv = brain.EMOTIONS.get(emo, (0, 0, 0))
                rate += _round_half(dr * k)
                pitch += _round_half(dp * kp)
                vol += _round_half(dv * k)
                if sent.rstrip("»”\"' ").endswith(("...", "…")):
                    extra = 300
            elif s.kind == "narr" and st.scene_moods:
                dr, dp, dv = brain.MOOD_PROSODY.get(s.mood, (0, 0, 0))
                er, ep, ev, extra = brain.sentence_extra(sent) if st.emotions else (0, 0, 0, 0)
                rate += _round_half((dr + er) * k)
                pitch += _round_half((dp + ep) * kp)
                vol += _round_half((dv + ev) * k)
                emo = s.mood
            rate = max(-50, min(200, rate))
            pitch = max(-50, min(50, pitch))
            gain = max(0.2, min(2.0, 1 + vol / 100))
            extra = int(extra * k / sp)
            last_sent = si == len(sents) - 1
            p = (seg_pause if last_sent else int(220 / sp)) + extra
            txt = apply_lexicon(apply_pron(sent, pron), lex)
            last = out[-1] if out else None
            if (last and last.voice == voice and last.rate == rate and last.pitch == pitch
                    and abs(last.gain - gain) < 0.005 and not last.hard and last.pause_ms <= pauses["para"]
                    and len(last.text) + len(txt) < MAX_CHARS and s.kind != "title"):
                sep = "\n" if last.pause_ms >= int(pauses["para"] / sp) else " "
                last.text += sep + txt
                last.src_end, last.pause_ms, last.hard = s.end, p, extra > 0
                continue
            parts = _split_long(txt)
            for i, part in enumerate(parts):
                fin = i == len(parts) - 1
                out.append(Chunk(part, voice, rate, pitch, gain, p if fin else 60, s.start, s.end, who,
                                 chapter, emo, extra > 0 and fin))
    return out


def plan(text, segs, chapters, st):
    """chapters: [(titulo, inicio, fin)] -> lista de chunks con índice de capítulo."""
    chunks = []
    for i, (_, a, b) in enumerate(chapters):
        chunks.extend(build_chunks(segs, st, a, b, i))
    return chunks


# ---------------------------------------------------------------------------
# Síntesis
# ---------------------------------------------------------------------------

class Cancelled(Exception):
    pass


def _fmt(n, unit):
    return f"{'+' if n >= 0 else ''}{int(n)}{unit}"


def _tidy(t):
    t = re.sub(r"[^\w\s.,;:¡!¿?…'\"«»“”()\-—]", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip() or "."


async def _tts(ch, cancel):
    last = None
    empty = 0
    text = ch.text
    for attempt in range(6):
        if cancel.is_set():
            raise Cancelled()
        try:
            comm = edge_tts.Communicate(text, ch.voice, rate=_fmt(ch.rate, "%"),
                                        pitch=_fmt(ch.pitch, "Hz"), boundary="SentenceBoundary",
                                        receive_timeout=90)
            audio = bytearray()
            marks = []
            async for item in comm.stream():
                if cancel.is_set():
                    raise Cancelled()
                if item["type"] == "audio":
                    audio.extend(item["data"])
                elif item["type"] in ("SentenceBoundary", "WordBoundary"):
                    marks.append((item["offset"] / 1e7, item["duration"] / 1e7, item["text"]))
            if audio:
                return bytes(audio), marks
            last = RuntimeError("el servicio no devolvió audio")
            empty += 1
        except Cancelled:
            raise
        except Exception as e:
            last = e
            if type(e).__name__ == "NoAudioReceived":
                empty += 1
        if empty:
            # texto que la voz no puede leer: un intento con texto limpio y, si tampoco, se salta
            if empty >= 2:
                SKIPPED.append(ch.text[:80])
                return b"", []
            text = _tidy(ch.text)
            continue
        await asyncio.sleep(min(20, 1.5 * 2 ** attempt))
    raise RuntimeError(f"No se pudo sintetizar un fragmento ({ch.voice}): {last}")


SKIPPED = []   # fragmentos que la voz no pudo leer (se reemplazan por un silencio corto)


# ---------------------------------------------------------------------------
# Voz natural sin internet: la misma de la APK («Kuon natural · México», Piper es_MX claude con sherpa-onnx).
# Una sola voz; los personajes se distinguen con un cambio de tono, con la misma fórmula que engine.js
# y NaturalVoice.java para que la PC y el teléfono suenen igual.
# ---------------------------------------------------------------------------

OFFLINE_NAME = "Kuon natural · México"
FEMALE_RE = re.compile(r"Female|Dalia|Paloma|Salome|Camila|Paola|Elena|Catalina|Sofia|Andrea|Maria|Margarita|"
                       r"Valentina|Tania|Marta|Karla|Yolanda|Lorena|Ramona|Karina|Belkys|Elvira|Ximena|Teresa|Ava|"
                       r"Emma|Jenny|Aria|Sonia|Francisca|Denise|Vivienne|Seraphina|Katja|Elsa|Nanami|SunHi|Xiaoxiao")
_off = {"tts": None, "err": "", "dir": None}
_off_load_lock = threading.Lock()
_off_gen_lock = threading.Lock()


def voz_dir():
    """Carpeta «voz» junto al .exe (instalado), junto al código o dentro del paquete."""
    if _off["dir"] is None:
        _off["dir"] = ""
        for d in (os.path.dirname(os.path.abspath(sys.executable)), os.path.dirname(os.path.abspath(__file__)),
                  getattr(sys, "_MEIPASS", "")):
            v = os.path.join(d, "voz") if d else ""
            if v and os.path.isfile(os.path.join(v, "tokens.txt")) and os.path.isdir(os.path.join(v, "espeak-ng-data")) \
                    and any(n.endswith(".onnx") for n in os.listdir(v)):
                _off["dir"] = v
                break
    return _off["dir"]


def offline_available():
    if not voz_dir() or _off["err"]:
        return False
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception as e:
        _off["err"] = str(e)
        return False


def _offline_engine():
    with _off_load_lock:
        if _off["tts"] is None:
            import sherpa_onnx
            v = voz_dir()
            if not v:
                raise RuntimeError("Falta la carpeta «voz» con la voz natural. Reinstala el programa.")
            model = next(n for n in os.listdir(v) if n.endswith(".onnx"))

            def make(provider):
                return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                    model=sherpa_onnx.OfflineTtsModelConfig(
                        vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=os.path.join(v, model),
                                                                   tokens=os.path.join(v, "tokens.txt"),
                                                                   data_dir=os.path.join(v, "espeak-ng-data")),
                        num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)), provider=provider),
                    max_num_sentences=2))
            # tarjeta dedicada si la hay (cuda / directml); si la librería no la admite, ella misma usa el procesador
            prov = hardware.onnx_provider()
            try:
                _off["tts"] = make(prov)
            except Exception:
                if prov == "cpu":
                    raise
                _off["tts"] = make("cpu")
        return _off["tts"]


def offline_params(ch):
    """(velocidad, tono) como engine.js: el narrador tal cual; los personajes un poco más agudos o graves."""
    is_char = bool(ch.who)
    female = is_char and bool(FEMALE_RE.search(ch.voice))
    rate = 1 + ch.rate / 100
    pitch = 1 + ((ch.pitch / 120 + (0.16 if female else -0.03)) if is_char else ch.pitch / 150)
    pitch = max(0.6, min(1.6, pitch))
    p = max(0.75, min(1.35, pitch))            # NaturalVoice.java
    return max(0.4, min(3.0, rate / p)), p


def _wav_bytes(samples, sr):
    pcm = (np.clip(np.asarray(samples, np.float32), -1, 1) * 32767).astype("<i2").tobytes()
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return b.getvalue()


async def _tts_offline(ch, cancel):
    if cancel.is_set():
        raise Cancelled()

    def run():
        eng = _offline_engine()
        speed, p = offline_params(ch)
        text = _tidy(ch.text) if not re.search(r"\w", ch.text) else ch.text
        with _off_gen_lock:                       # el motor no admite dos frases a la vez
            if cancel.is_set():
                raise Cancelled()
            a = eng.generate(text, sid=0, speed=speed)
        s = np.asarray(a.samples, np.float32)
        if s.size < 200:
            return b""
        return _wav_bytes(s, round(a.sample_rate * p))   # cambiar la frecuencia sube o baja el tono
    data = await asyncio.to_thread(run)
    if not data:
        SKIPPED.append(ch.text[:80])
    return data, []


_net = {"t": 0.0, "ok": True}


def internet_ok():
    """¿Se llega al servicio de voces de Microsoft? (se recuerda 20 s)"""
    if time.time() - _net["t"] < 20:
        return _net["ok"]
    try:
        socket.create_connection(("speech.platform.bing.com", 443), timeout=2.5).close()
        ok = True
    except OSError:
        ok = False
    _net.update(t=time.time(), ok=ok)
    return ok


def use_offline(st):
    """Automático decide al empezar: Microsoft si hay internet, la voz natural si no (como la APK)."""
    if st.voice_mode == "offline":
        return True
    if st.voice_mode == "online":
        return False
    return not internet_ok() and offline_available()


def _net_error(e):
    m = f"{type(e).__name__} {e}"
    return bool(re.search(r"Cannot connect|getaddrinfo|ClientConnector|Temporary failure|speech\.platform|"
                          r"timed out|Timeout|ConnectionError|ConnectionReset|No route|Network is unreachable|"
                          r"WSServerHandshake|ServerDisconnected", m, re.I))


def decode_mp3(data, sr=SR_TTS):
    import miniaudio
    d = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=sr)
    return np.frombuffer(d.samples, dtype=np.float32).copy()


def load_music(path, sr):
    import miniaudio
    d = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=sr)
    m = np.frombuffer(d.samples, dtype=np.float32).copy()
    peak = float(np.max(np.abs(m))) if m.size else 0
    return m / peak * 0.8 if peak > 0 else m


def trim_and_level(x, gain, sr=SR_TTS):
    """Recorta silencios del servicio, iguala volumen y suaviza bordes. Devuelve (audio, recorte_inicial_s)."""
    if x.size == 0:
        return x, 0.0
    fr = sr // 100
    n = x.size // fr
    if n < 3:
        return x, 0.0
    frames = x[: n * fr].reshape(n, fr)
    peak = np.abs(frames).max(axis=1)
    act = np.where(peak > 0.015)[0]
    if act.size == 0:
        return np.zeros(0, np.float32), 0.0
    a = max(0, act[0] - 3) * fr
    b = min(x.size, (act[-1] + 6) * fr)
    y = x[a:b].astype(np.float32)
    f2 = y[: (y.size // fr) * fr].reshape(-1, fr)
    rms = np.sqrt((f2 ** 2).mean(axis=1))
    voiced = rms[rms > 0.02]
    level = float(np.sqrt((voiced ** 2).mean())) if voiced.size else float(rms.mean() + 1e-6)
    g = min(3.0, max(0.4, 0.11 / max(level, 1e-4))) * gain
    pk = float(np.abs(y).max()) * g
    if pk > 0.97:
        g *= 0.97 / pk
    y *= g
    fade = min(y.size // 4, int(0.008 * sr))
    if fade > 1:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]
    return y, a / sr


def resample(x, src, dst):
    if src == dst or x.size == 0:
        return x
    n = int(round(x.size * dst / src))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)


class Writer:
    def __init__(self, path, sr, fmt, bitrate):
        self.fmt, self.sr = fmt, sr
        if fmt == "mp3":
            import lameenc
            self.enc = lameenc.Encoder()
            self.enc.set_in_sample_rate(sr)
            self.enc.set_channels(1)
            self.enc.set_bit_rate(int(bitrate))
            self.enc.set_quality(2)
            self.f = open(path, "wb")
        else:
            self.f = wave.open(path, "wb")
            self.f.setnchannels(1)
            self.f.setsampwidth(2)
            self.f.setframerate(sr)

    wrote = False

    def write(self, x):
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        if not pcm:
            return
        self.wrote = True
        if self.fmt == "mp3":
            self.f.write(self.enc.encode(pcm))
        else:
            self.f.writeframes(pcm)

    def close(self):
        # lameenc falla al vaciar un codificador que no recibió nada (sin internet, cancelar al instante):
        # el archivo se cierra igual, si no Windows no deja borrar el .part y queda en tu carpeta de audios
        try:
            if self.fmt == "mp3" and self.wrote:
                self.f.write(self.enc.flush())
        finally:
            self.f.close()


class MusicBed:
    """Música en bucle con fundido de entrada y 'ducking' bajo la voz."""

    def __init__(self, path, sr, db, duck):
        self.m = load_music(path, sr)
        self.sr, self.pos = sr, 0
        self.gain = 10 ** (db / 20)
        self.duck = duck
        self.env = 1.0
        self.t = 0

    def take(self, n):
        if self.m.size == 0:
            return np.zeros(n, np.float32)
        out = np.empty(n, np.float32)
        i = 0
        while i < n:
            k = min(n - i, self.m.size - self.pos)
            out[i:i + k] = self.m[self.pos:self.pos + k]
            self.pos = (self.pos + k) % self.m.size
            i += k
        return out

    def mix(self, voice):
        n = voice.size
        bed = self.take(n) * self.gain
        fr = self.sr // 100
        frames = (n + fr - 1) // fr
        g = np.ones(frames, np.float32)
        if self.duck:
            pad = np.zeros(frames * fr, np.float32)
            pad[:n] = voice
            rms = np.sqrt((pad.reshape(frames, fr) ** 2).mean(axis=1))
            env = self.env
            for i in range(frames):
                target = 0.42 if rms[i] > 0.015 else 1.0
                coef = 0.35 if target < env else 0.03   # baja rápido, sube despacio
                env += (target - env) * coef
                g[i] = env
            self.env = env
        gs = np.repeat(g, fr)[:n]
        t = np.arange(self.t, self.t + n) / self.sr
        fade_in = np.clip(t / 2.5, 0, 1).astype(np.float32)
        self.t += n
        return voice + bed * gs * fade_in

    def tail(self, seconds=3.0):
        n = int(seconds * self.sr)
        bed = self.take(n) * self.gain * self.env
        return bed * np.linspace(1, 0, n, dtype=np.float32)


@dataclass
class Result:
    duration: float
    chunk_times: list       # [(t0, t1)] por chunk
    marks: list             # [(t0, t1, texto, idx_chunk)]
    envelope: list          # rms cada 50 ms
    chapter_times: dict     # {capitulo: t0}
    spectrum: bytes = b""   # SPEC_BANDS bytes (0-255) por cada trama de 50 ms


SPEC_BANDS = 32
_band_cache = {}


def _bands(sr, hop):
    key = (sr, hop)
    if key not in _band_cache:
        freqs = np.fft.rfftfreq(hop, 1 / sr)
        edges = np.geomspace(70, min(9000, sr / 2 - 1), SPEC_BANDS + 1)
        idx = np.clip(np.searchsorted(edges, freqs) - 1, -1, SPEC_BANDS)
        m = np.zeros((freqs.size, SPEC_BANDS), np.float32)
        for b in range(SPEC_BANDS):
            sel = idx == b
            if not sel.any():
                sel[np.argmin(np.abs(freqs - edges[b]))] = True
            m[sel, b] = 1.0 / sel.sum()
        _band_cache[key] = (np.hanning(hop).astype(np.float32), m)
    return _band_cache[key]


def spectrum_frames(frames, sr):
    """frames: (k, hop) -> bytes k*SPEC_BANDS con la energía por banda (escala log, 0-255)."""
    win, m = _bands(sr, frames.shape[1])
    mag = np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float32) @ m
    db = 20 * np.log10(mag + 1e-6)
    v = np.clip((db + 18) / 52, 0, 1)
    return (v * 255).astype(np.uint8).tobytes()


def _ts(sec):
    h, r = divmod(max(sec, 0), 3600)
    m, s = divmod(r, 60)
    ms = min(999, int(round((s - int(s)) * 1000)))
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def yt_stamp(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


CACHE_DIR = None


def _cache_path(ch, offline=False):
    if not CACHE_DIR:
        return None
    import hashlib
    key = f"{ch.voice}|{ch.rate}|{ch.pitch}|{ch.text}"
    if offline:
        key = "natural|" + ("char" if ch.who else "narr") + "|" + key
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, h[:2], h + (".wav" if offline else ".mp3"))


async def _cached_tts(ch, cancel, tts, offline=False):
    cp = _cache_path(ch, offline)
    if cp and os.path.exists(cp):
        try:
            with open(cp, "rb") as f:
                data = f.read()
            with open(cp + ".m", "r", encoding="utf-8") as f:
                import json
                marks = [tuple(x) for x in json.load(f)]
            if data:
                try:
                    os.utime(cp)        # «usado hace poco»: la limpieza borra primero lo que no se escucha
                except OSError:
                    pass
                return data, marks
        except Exception:
            pass
    data, marks = await tts(ch, cancel)
    complete = True
    if not data:
        # fragmento largo que la voz no lee: frase por frase, y solo se salta la que falla
        parts = [p for p in re.split(r"(?<=[.!?…»”])\s+|\n+", ch.text) if re.search(r"\w", p)]
        if len(parts) > 1:
            got = []
            for p in parts:
                d2, _ = await tts(Chunk(p, ch.voice, ch.rate, ch.pitch, ch.gain, 0, 0, 0, ch.who), cancel)
                if d2:
                    got.append(d2)
                else:
                    complete = False
            data, marks = b"".join(got), []
    if cp and data and complete:     # lo que salió con huecos no se guarda: la próxima vez se reintenta
        try:
            import json
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            with open(cp + ".m.tmp", "w", encoding="utf-8") as f:
                json.dump(marks, f)
            os.replace(cp + ".m.tmp", cp + ".m")
            with open(cp + ".tmp", "wb") as f:      # si la PC se apaga a mitad, no queda un MP3 cortado en la caché
                f.write(data)
            os.replace(cp + ".tmp", cp)
        except Exception:
            pass
    return data, marks


def cached_count(chunks, offline=False):
    n = 0
    for c in chunks:
        cp = _cache_path(c, offline)
        if cp and os.path.exists(cp):
            n += 1
    return n


def trim_cache(limit_bytes=3 * 1024 ** 3):
    if not CACHE_DIR or not os.path.isdir(CACHE_DIR):
        return
    files = []
    for root, _, fs in os.walk(CACHE_DIR):
        for f in fs:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))     # mtime: se renueva en cada uso (atime suele estar apagado en Windows)
            except OSError:
                pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= limit_bytes:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def render(chunks, out_path, st, fmt="mp3", progress=None, cancel=None, workers=4, tts=None,
           bed=None, lead=True, tail=True):
    """Sintetiza y une todo. Devuelve Result. tts permite inyectar un sintetizador (pruebas)."""
    cancel = cancel or threading.Event()
    injected = tts is not None
    base_tts = tts or _tts
    # La fuente se decide una vez al empezar y no se mezcla (igual que la APK), salvo que el usuario
    # elija «Seguir con la voz natural» si internet se cae a mitad.
    sess = {"offline": (not injected) and use_offline(st), "ok": False}

    async def tts(ch, cancel):
        if sess["offline"]:
            return await _cached_tts(ch, cancel, _tts_offline, offline=True)
        try:
            r = await _cached_tts(ch, cancel, base_tts)
            sess["ok"] = True
            return r
        except Cancelled:
            raise
        except Exception as e:
            if injected or st.voice_mode == "online" or not _net_error(e) or not offline_available():
                raise
            if not sess["ok"] or st.on_drop == "switch":
                sess["offline"] = True            # Microsoft no responde: sigue con la voz natural del PC
                _net.update(t=time.time(), ok=False)
                return await _cached_tts(ch, cancel, _tts_offline, offline=True)
            while not cancel.is_set():            # «Esperar a que vuelva»
                await asyncio.sleep(5)
                try:
                    return await _cached_tts(ch, cancel, base_tts)
                except Cancelled:
                    raise
                except Exception as e2:
                    if not _net_error(e2):
                        raise
            raise Cancelled()
    total = len(chunks)
    results, errors = {}, []
    lock = threading.Condition()

    async def run():
        sem = asyncio.Semaphore(workers)
        done = 0

        async def one(i, ch):
            nonlocal done
            async with sem:
                r = await tts(ch, cancel)
            with lock:
                results[i] = r
                lock.notify_all()
            done += 1
            if progress:
                progress(done, total)

        tasks = [asyncio.create_task(one(i, c)) for i, c in enumerate(chunks)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            raise

    def runner():
        try:
            asyncio.run(run())
        except BaseException as e:
            errors.append(e)
        with lock:
            lock.notify_all()

    th = threading.Thread(target=runner, daemon=True)
    th.start()

    sr = st.out_rate
    tmp = out_path + ".part"
    wr = Writer(tmp, sr, fmt, st.bitrate)
    if bed is None and st.music_path and os.path.exists(st.music_path):
        try:
            bed = MusicBed(st.music_path, sr, st.music_db, st.duck)
        except Exception:
            bed = None
    elif bed is False:
        bed = None
    t = 0.0
    chunk_times, marks, env, chapter_times = [], [], [], {}
    spec = bytearray()
    env_buf = np.zeros(0, np.float32)
    hop = int(sr * 0.05)
    lead_pad = np.zeros(int(0.35 * sr), np.float32)
    first = lead
    try:
        for i, ch in enumerate(chunks):
            with lock:
                while i not in results and not errors:
                    lock.wait(0.2)
                    if cancel.is_set():
                        raise Cancelled()
                if errors:
                    raise errors[0]
                mp3, mk = results.pop(i)
            try:
                y, cut = trim_and_level(decode_mp3(mp3), ch.gain) if mp3 else (np.zeros(int(0.25 * SR_TTS), np.float32), 0)
            except Exception:
                for cp in (_cache_path(ch), _cache_path(ch, True)):
                    if cp and os.path.exists(cp):
                        try:
                            os.remove(cp)
                        except OSError:
                            pass
                SKIPPED.append(ch.text[:80])
                y, cut = np.zeros(int(0.25 * SR_TTS), np.float32), 0
            y = resample(y, SR_TTS, sr)
            block = np.concatenate([lead_pad if first else np.zeros(0, np.float32), y,
                                    np.zeros(int(ch.pause_ms / 1000 * sr), np.float32)])
            t0 = t + (lead_pad.size / sr if first else 0)
            first = False
            chapter_times.setdefault(ch.chapter, t0)
            chunk_times.append((t0, t0 + y.size / sr))
            for off, dur, txt in mk:
                a = t0 + off - cut
                marks.append((a, a + max(dur, 0.3), txt, i))
            if not mk and ch.text.strip():
                # la voz sin internet no avisa dónde empieza cada frase: se reparte el tiempo del bloque por frases
                d = y.size / sr
                parts = [p for p in re.split(r"(?<=[.!?…])\s+", ch.text.strip()) if p.strip()] or [ch.text.strip()]
                tot, acc = sum(len(p) for p in parts) or 1, 0
                for ptxt in parts:
                    a = t0 + d * acc / tot
                    acc += len(ptxt)
                    marks.append((a, t0 + d * acc / tot, ptxt, i))
            if bed:
                block = bed.mix(block)
            wr.write(block)
            env_buf = np.concatenate([env_buf, block])
            k = env_buf.size // hop
            if k:
                fr = env_buf[:k * hop].reshape(k, hop)
                env.extend(np.sqrt((fr ** 2).mean(axis=1)).round(4).tolist())
                spec += spectrum_frames(fr, sr)
                env_buf = env_buf[k * hop:]
            t += block.size / sr
        if bed and tail:
            tl = bed.tail()
            wr.write(tl)
            t += tl.size / sr
        wr.close()
    except BaseException:
        cancel.set()
        try:
            wr.close()
        except Exception:
            pass
        try:
            os.remove(tmp)
        except OSError:
            pass
        th.join(5)
        raise
    th.join()
    os.replace(tmp, out_path)
    return Result(t, chunk_times, marks, env, chapter_times, bytes(spec))


def write_srt(res, path):
    lines = []
    for n, (a, b, txt, _) in enumerate(res.marks, 1):
        lines.append(f"{n}\n{_ts(a)} --> {_ts(b)}\n{txt.strip()}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_stamps(res, chapters, path, title="", cast=None):
    rows = [f"{yt_stamp(res.chapter_times[i])} {chapters[i][0]}"
            for i in sorted(res.chapter_times) if i < len(chapters)]
    with open(path, "w", encoding="utf-8") as f:
        if title:
            f.write(f"{title}\n\n")
        f.write("CAPÍTULOS\n" + "\n".join(rows) + "\n")
        if cast:
            f.write("\nVOCES\n")
            for nm, c in cast.items():
                f.write(f"{nm}: {c['voice'].split('-', 2)[2].replace('Neural', '')}\n")
        f.write("\nNarración generada con Audio Novela Studio (voces neurales de Microsoft).\n")
