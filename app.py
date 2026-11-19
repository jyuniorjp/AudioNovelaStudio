# -*- coding: utf-8 -*-
"""
Audio Novela Studio — estudio de narración de novelas y fanfics para YouTube.
Aplicación de escritorio de Windows (sin navegador).
"""
import sys

if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--asr":
    # el mismo programa hace de «trabajador» de reconocimiento de voz en un proceso aparte (ver asr_worker.py)
    import asr_worker
    asr_worker.main(sys.argv[2])
    sys.exit(0)

import bisect
import dataclasses
import json
import os
import re
import tempfile
import threading
import math
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

import ui_scroll

ui_scroll.install()             # la rueda mueve solo lo que está bajo el ratón (ver ui_scroll.py)

import audio
import estudio
import brain
from assistant_ui import AssistantMixin
import docs
import ia_dudosos
import ocr
from subs_ui import SubsMixin
import repaso
import video
import imagen_ia
import localai
import assistant as ai
import voices as vx
try:
    from version import APP_VERSION
except Exception:
    APP_VERSION = {"version": "0.4.0", "label": "0.4 demo", "build": 6, "schema": 2, "notes": []}

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_OK = True
except Exception:
    DND_OK = False

try:
    import pygame
    pygame.mixer.pre_init(44100, -16, 1, 1024)
    pygame.mixer.init()
    AUDIO_OK = True
except Exception:
    AUDIO_OK = False

APP = "Audio Novela Studio"
MATH_LABEL = {"auto": "Automático", "on": "Siempre", "off": "Nunca"}
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AudioNovelaStudio")
AI_BG = "Imágenes con IA (por capítulo)"
CFG_FILE = os.path.join(CFG_DIR, "config.json")
TMP = os.path.join(tempfile.gettempdir(), "AudioNovelaStudio")
os.makedirs(TMP, exist_ok=True)


def _drop_stream_file(path):
    """Borra un trozo temporal de la escucha. Solo toca stream_*.wav de la carpeta temporal, nunca un MP3 tuyo."""
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(TMP) and os.path.basename(path).startswith("stream_"):
        try:
            os.remove(path)
        except OSError:
            pass


def _clean_stream_leftovers(hours=6):
    """Restos de escuchas anteriores (si la app se cerró de golpe). Los recientes se dejan por si hay otra ventana abierta."""
    try:
        lim = time.time() - hours * 3600
        for n in os.listdir(TMP):
            f = os.path.join(TMP, n)
            if n.startswith("stream_") and os.path.getmtime(f) < lim:
                _drop_stream_file(f)
    except OSError:
        pass
MEM_FILE = os.path.join(CFG_DIR, "kuon_memoria.json")
audio.CACHE_DIR = os.path.join(CFG_DIR, "cache_voces")


def pron_pack(name, pron, lexicon):
    """Paquete de dicción compartible entre obras, la PC y el teléfono (mismo formato en app.js)."""
    return {"tipo": "kuon_diccion", "version": 1, "nombre": name, "pron": dict(pron),
            "lexico": [l for l in (lexicon or "").splitlines() if "=" in l and not l.strip().startswith("#")]}


def merge_pron_pack(d, pron, lexicon):
    """Suma el paquete sin pisar lo que el usuario ya eligió. Devuelve (palabras nuevas, léxico unido)."""
    if not isinstance(d, dict) or d.get("tipo") != "kuon_diccion":
        raise ValueError("falta «tipo: kuon_diccion»")
    added = 0
    for w, how in (d.get("pron") or {}).items():
        if w and w not in pron:
            pron[w] = how
            added += 1
    have = {l.split("=", 1)[0].strip().lower() for l in lexicon.splitlines() if "=" in l}
    extra = [l for l in d.get("lexico") or [] if "=" in l and l.split("=", 1)[0].strip().lower() not in have]
    lines = lexicon.rstrip("\n") + ("\n" if lexicon.strip() and extra else "") + "\n".join(extra)
    return added, lines


def _keep_broken(path):
    """Un archivo que no se pudo leer se guarda como .bak antes de que la app lo sobrescriba con uno vacío."""
    if os.path.exists(path):
        try:
            os.replace(path, path + time.strftime(".%Y%m%d-%H%M%S.bak"))
        except OSError:
            pass


def load_memory():
    m = brain.new_memory()
    try:
        with open(MEM_FILE, encoding="utf-8") as f:
            m.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception:
        _keep_broken(MEM_FILE)
    return repair_memory(m)


def repair_memory(m):
    """Arregla memorias de versiones anteriores: fusiones en círculo (henrietta = Henrietta = henrietta),
    epítetos que no son personas («el pelo», «la puerta») y nombres descartados que el usuario usa."""
    try:
        al, canon = brain.clean_aliases(m.get("aliases", {}))
        m["aliases"] = al
        fixn = lambda n: al.get(canon.get(n.lower(), n), canon.get(n.lower(), n)) if n else n
        for pj, eps in list(m.get("epithets", {}).items()):
            m["epithets"][pj] = {e: fixn(n) for e, n in eps.items() if n and brain.epithet_ok(e)}
        m["genders"] = {fixn(k): v for k, v in m.get("genders", {}).items()}
        used = {v.lower() for v in al.values()}
        m["not_names"] = [n for n in m.get("not_names", []) if n.lower() not in used]
    except Exception:
        pass
    return m


def save_memory(m):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = MEM_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEM_FILE)
    except Exception:
        pass


def project_of(path):
    """Carpeta de la obra: si el archivo está en 'CAPITULO 01', la obra es la carpeta de arriba."""
    if not path:
        return "_sin_titulo"
    d = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(d)
    if re.search(r"cap[ií]tulo|chapter|^cap\b|^\d+$|bloque|parte", name, re.IGNORECASE):
        d = os.path.dirname(d)
    return os.path.basename(d) or "_sin_titulo"

# Paleta "neón nocturno"
BG = "#070a12"
SURF = "#0c1221"
SURF2 = "#111a2e"
SURF3 = "#18233c"
LINE = "#1f2d4d"
TXT = "#e7edff"
MUTED = "#7f8db3"
CYAN = "#22d3ee"
CYAN_D = "#0b7285"
VIOLET = "#a78bfa"
AMBER = "#f5a524"
RED = "#f87171"
GREEN = "#34d399"
CHAR_COLORS = ["#22d3ee", "#f472b6", "#a78bfa", "#34d399", "#f5a524", "#60a5fa", "#fb7185",
               "#facc15", "#4ade80", "#c084fc", "#38bdf8", "#fda4af", "#2dd4bf", "#fb923c"]

# «Clásico» se inspira en el narrador de Zueira's Voice: lectura clara y pareja, con emociones suaves.
PRESETS = {
    "Clásico": dict(speed=1.3, pitch=0, volume=0, expressive=True, emo=0.6),
    "Épico": dict(speed=1.75, pitch=-6, volume=0, expressive=True, emo=1.15),
    "Audiolibro": dict(speed=1.2, pitch=0, volume=0, expressive=True, emo=0.85),
    "Suspenso": dict(speed=1.35, pitch=-8, volume=-4, expressive=True, emo=1.1),
    "Acción": dict(speed=1.9, pitch=-3, volume=4, expressive=True, emo=1.25),
    "Romance": dict(speed=1.15, pitch=-2, volume=-2, expressive=True, emo=1.0),
}


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """Mezcla dos colores hex (t=0 -> a, t=1 -> b)."""
    ra, ga, ba = _hex(a)
    rb, gb, bb = _hex(b)
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % (int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))
AGES = ["niño", "joven", "adulto", "anciano"]
EMO_CHOICES = [("", "Automática"), ("neutral", "Neutral"), ("susurro", "Susurro"), ("grito", "Grito"),
               ("enojo", "Enojo"), ("triste", "Tristeza"), ("alegre", "Alegría"), ("miedo", "Miedo"),
               ("ternura", "Ternura"), ("duda", "Duda"), ("sorpresa", "Sorpresa"), ("frio", "Frialdad"),
               ("pensamiento", "Pensamiento")]

ctk.set_appearance_mode("dark")


def font(size=13, weight="normal", family="Segoe UI"):
    return ctk.CTkFont(family=family, size=size, weight=weight)


def load_cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        _keep_broken(CFG_FILE)
        return {}


def save_cfg(d):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CFG_FILE)
    except Exception:
        pass


Base = ctk.CTk
if DND_OK:
    class Base(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:
                pass


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_base():
    """Memoria precargada de Kuon IA, entrenada con las obras del usuario (kuon_base.json)."""
    for d in (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None,
              os.path.dirname(os.path.abspath(__file__)), getattr(sys, "_MEIPASS", None)):
        if d and os.path.exists(os.path.join(d, "kuon_base.json")):
            try:
                with open(os.path.join(d, "kuon_base.json"), encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


BASE_MEMORY = load_base()
_IMG = {}


def img(name, size):
    """Arte de la 1.0 (carpeta img/). Devuelve None si falta Pillow o el archivo."""
    k = (name, size)
    if k not in _IMG:
        try:
            from PIL import Image
            p = resource(os.path.join("img", name + ".png"))
            _IMG[k] = ctk.CTkImage(Image.open(p), size=(size, size)) if os.path.exists(p) else None
        except Exception:
            _IMG[k] = None
    return _IMG[k]


# ===========================================================================
class Studio(AssistantMixin, SubsMixin, Base):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.cfg = load_cfg()
        self.title(f"{APP} · {APP_VERSION['label']}")
        self.geometry(self.cfg.get("geometry", "1440x880"))
        self.minsize(1180, 700)
        ico = resource("icon.ico")
        if os.path.exists(ico):
            self.after(300, lambda: self._safe(lambda: self.iconbitmap(ico)))

        self.voices = [vx.Voice(n, g) for n, g in vx.FALLBACK]
        self.voices_online = False
        self.doc_path = None
        self.doc_key = "_sin_titulo"
        self.segs, self.chars, self.chapters = [], {}, [("Texto completo", 0, 0)]
        self.cast, self.overrides, self.aliases = {}, {}, {}
        self.memory = load_memory()
        self.project = "_sin_titulo"
        self.info = {}
        threading.Thread(target=audio.trim_cache, daemon=True).start()
        self.char_color = {}
        self.cancel = threading.Event()
        self.busy = False
        self.play = None            # {'chunks','result','path','t0','paused_at'}
        self.paused = False
        self.analyze_job = None
        self.analysis_gen = 0
        self.last_output = None
        self.ollama_models = None
        self._line_starts = [0]
        self._cards = {}
        self._card_sig = {}
        self._hist, self._fut = [], []         # deshacer / rehacer
        self._last_ck = ("", 0.0)
        self._save_job = None
        self._resize_until = 0.0

        self._vars()
        self._build()
        self._apply_cfg()
        self._bind_dnd()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq in ("<Control-z>", "<Control-Z>"):
            self.bind_all(seq, self._key_undo, add="+")
        for seq in ("<Control-y>", "<Control-Y>", "<Control-Shift-Z>", "<Control-Shift-z>"):
            self.bind_all(seq, self._key_redo, add="+")
        self.bind("<Configure>", self._on_resize, add="+")
        self._bind_keys()
        self.after(60, self._tick)
        threading.Thread(target=self._load_voices, daemon=True).start()
        threading.Thread(target=self._check_ollama, daemon=True).start()
        if self.cfg.get("read_theme", "neon") != "neon":
            self.apply_read_theme(save=False)
        if not self.cfg.get("tour_done") and not self.cfg.get("lang"):
            self.after(1200, self.start_tour)
        if self.cfg.get("seen_version") != APP_VERSION["label"]:
            self.cfg["seen_version"] = APP_VERSION["label"]
            if APP_VERSION.get("notes") and self.cfg.get("lang"):   # solo a quien actualiza, no en la primera vez
                self._news_pending = True
                self.after(1500, self._news_then_open)
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            if not getattr(self, "_news_pending", False):
                self.after(400, lambda: self.load_file(sys.argv[1]))
        else:
            self._show_drop(True)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    # ------------------------------------------------------------ variables
    def _vars(self):
        c = self.cfg
        self.v_variant = tk.StringVar(value=c.get("variant") or vx.variant_from_legacy(c.get("lang", "es"),
                                                                                       c.get("neutral", True)))
        self.v_lang = tk.StringVar(value=vx.variant_info(self.v_variant.get())[2])
        self.v_lang_label = tk.StringVar()
        self.v_narr = tk.StringVar()
        self.v_gm = tk.StringVar()
        self.v_gf = tk.StringVar()
        self.v_speed = tk.DoubleVar(value=c.get("speed", 1.75))
        self.v_pitch = tk.DoubleVar(value=c.get("pitch", -8))
        self.v_vol = tk.DoubleVar(value=c.get("volume", 0))
        self.v_preset = tk.StringVar(value=c.get("preset", "Épico") if c.get("preset") in PRESETS else "Épico")
        self.v_charvoices = tk.BooleanVar(value=c.get("character_voices", True))
        self.v_autocast = tk.BooleanVar(value=c.get("autocast", True))
        self.v_emotions = tk.BooleanVar(value=c.get("emotions", True))
        self.v_moods = tk.BooleanVar(value=c.get("scene_moods", True))
        self.v_skip = tk.BooleanVar(value=c.get("skip_tags", False))
        self.v_skipnotes = tk.BooleanVar(value=c.get("skip_notes", True))
        self.v_skipbr = tk.BooleanVar(value=c.get("skip_brackets", False))
        mm = c.get("math_mode") or ("off" if c.get("math") is False else "auto")
        self.v_math = tk.StringVar(value=MATH_LABEL.get(mm, "Automático"))
        self.v_cites = tk.BooleanVar(value=c.get("skip_citations", True))
        self.v_titles = tk.BooleanVar(value=c.get("read_titles", True))
        self.v_colors = tk.BooleanVar(value=c.get("colors", True))
        self.v_p_inner = tk.DoubleVar(value=c.get("pause_inner", 160))
        self.v_p_para = tk.DoubleVar(value=c.get("pause_para", 420))
        self.v_p_scene = tk.DoubleVar(value=c.get("pause_scene", 1500))
        self.v_music = tk.StringVar(value=c.get("music_path", ""))
        self.v_music_db = tk.DoubleVar(value=c.get("music_db", -24))
        self.v_duck = tk.BooleanVar(value=c.get("duck", True))
        self.v_outdir = tk.StringVar(value=c.get("outdir") or self._default_outdir())
        self.v_fmt = tk.StringVar(value=c.get("fmt", "MP3"))
        self.v_bitrate = tk.StringVar(value=str(c.get("bitrate", 128)))
        self.v_split = tk.BooleanVar(value=c.get("split", False))
        self.v_srt = tk.BooleanVar(value=c.get("srt", True))
        self.v_yt = tk.BooleanVar(value=c.get("yt", True))
        self.v_mp4 = tk.BooleanVar(value=c.get("mp4", False))
        self.v_cover = tk.StringVar(value=c.get("cover", ""))
        self.v_vbg = tk.StringVar(value=c.get("video_bg", "Estrellas"))
        # una sola vez: estilo y modelo del video vuelven a «Automático» (ahora salen del prompt, como en el Estudio);
        # después, lo que elijas a mano se respeta
        auto_v2 = bool(c.get("video_auto_v2"))
        self.v_vstyle = tk.StringVar(value=c.get("video_ai_style") if auto_v2 and c.get("video_ai_style") in
                                     imagen_ia.STYLES else "Automático")
        self.v_vqual = tk.StringVar(value=c.get("video_ai_engine") if auto_v2 and c.get("video_ai_engine") in
                                    imagen_ia.ENGINES else imagen_ia.AUTO)
        self.v_vsubs = tk.BooleanVar(value=c.get("video_subs", True))
        self.v_vnames = tk.BooleanVar(value=c.get("video_names", True))
        self.v_vvert = tk.BooleanVar(value=c.get("video_vertical", False))
        self.v_workers = tk.DoubleVar(value=c.get("workers", 5))
        self.v_vmode = tk.StringVar(value={"online": "Microsoft", "offline": "Sin internet"}.get(
            c.get("voice_mode"), "Automático"))
        self.v_drop = tk.StringVar(value="Seguir con la voz natural" if c.get("on_drop") == "switch"
                                   else "Esperar a que vuelva")
        self.v_model = tk.StringVar(value=c.get("ollama_model", ""))
        self.v_threshold = tk.DoubleVar(value=c.get("threshold", 0.55))
        self.v_emo_int = tk.DoubleVar(value=c.get("emotion_intensity", 1.0))
        self.emo_over = {}
        self.heard = set()
        self.plan_chunks, self.plan_t0, self.plan_stat, self.plan_total = [], [0.0], [], 0.0
        self._plan_gen = 0
        self._plan_job = None
        self.pron = {}
        self.foreign = []

    def _default_outdir(self):
        cands = [os.path.join(os.path.expanduser("~"), "Music", "AudioNovelaStudio")]
        if os.path.isdir(r"D:\AudioNovelaStudio"):      # la PC donde se creó el proyecto
            cands.insert(0, r"D:\AudioNovelaStudio\Audios")
        for d in cands:
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except Exception:
                continue
        return os.path.expanduser("~")

    # ------------------------------------------------------------ construcción
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._header()
        main = ctk.CTkFrame(self, fg_color=BG)
        main.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self._editor(main)
        self._panel(main)
        self._player()

    def _header(self):
        h = ctk.CTkFrame(self, fg_color=BG, height=64)
        h.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        em = img("emblema", 46)
        if em:
            ctk.CTkLabel(h, text="", image=em).pack(side="left")
        else:
            logo = tk.Canvas(h, width=40, height=40, bg=BG, highlightthickness=0)
            logo.pack(side="left")
            logo.create_oval(2, 2, 38, 38, outline=CYAN, width=2)
            for i, hh in enumerate([8, 16, 24, 14, 20, 10]):
                x = 9 + i * 4.5
                logo.create_line(x, 20 - hh / 2, x, 20 + hh / 2, fill=CYAN if i % 2 == 0 else VIOLET,
                                 width=2.4, capstyle="round")
        t = ctk.CTkFrame(h, fg_color=BG)
        t.pack(side="left", padx=12)
        ctk.CTkLabel(t, text="AUDIO NOVELA STUDIO", font=font(20, "bold", "Bahnschrift"),
                     text_color=TXT).pack(anchor="w")
        ctk.CTkLabel(t, text=f"Kuon IA  ·  voces neurales  ·  versión {APP_VERSION['label']}",
                     font=font(11), text_color=MUTED).pack(anchor="w")
        self.lbl_doc = ctk.CTkLabel(h, text="Sin documento", font=font(12), text_color=MUTED,
                                    fg_color=SURF2, corner_radius=14, height=28)
        self.lbl_doc.pack(side="left", padx=18, ipadx=10)
        ctk.CTkButton(h, text="?", width=30, height=28, corner_radius=14, fg_color=SURF2, hover_color=CYAN_D,
                      text_color=CYAN, font=font(13, "bold"), command=self.show_help).pack(side="right", padx=(8, 0))
        self.btn_redo = ctk.CTkButton(h, text="↷", width=30, height=28, corner_radius=14, fg_color=SURF2,
                                      hover_color=CYAN_D, text_color=MUTED, font=font(14, "bold"),
                                      command=self.redo, state="disabled")
        self.btn_redo.pack(side="right", padx=(8, 0))
        self.btn_undo = ctk.CTkButton(h, text="↶", width=30, height=28, corner_radius=14, fg_color=SURF2,
                                      hover_color=CYAN_D, text_color=MUTED, font=font(14, "bold"),
                                      command=self.undo, state="disabled")
        self.btn_undo.pack(side="right", padx=(8, 0))
        self.chip_ai = ctk.CTkLabel(h, text="◉ Kuon IA", font=font(11, "bold"), text_color=GREEN,
                                    fg_color=SURF2, corner_radius=14, height=28)
        self.chip_ai.pack(side="right", padx=(8, 0), ipadx=10)
        self.chip_net = ctk.CTkLabel(h, text="◌ Cargando voces…", font=font(11), text_color=MUTED,
                                     fg_color=SURF2, corner_radius=14, height=28)
        self.chip_net.pack(side="right", ipadx=10)

    def _btn(self, parent, text, cmd, accent=False, width=0, **kw):
        if accent:
            return ctk.CTkButton(parent, text=text, command=cmd, fg_color=CYAN, hover_color="#67e8f9",
                                 text_color="#04121a", font=font(13, "bold"), corner_radius=10,
                                 height=34, width=width or 120, **kw)
        return ctk.CTkButton(parent, text=text, command=cmd, fg_color=SURF3, hover_color=LINE,
                             text_color=TXT, font=font(12), corner_radius=10, height=32,
                             width=width or 100, border_width=1, border_color=LINE, **kw)

    def _editor(self, parent):
        box = ctk.CTkFrame(parent, fg_color=SURF, corner_radius=16, border_width=1, border_color=LINE)
        box.grid(row=0, column=0, sticky="nsew")
        box.grid_rowconfigure(1, weight=1)
        box.grid_columnconfigure(0, weight=1)
        bar = ctk.CTkFrame(box, fg_color=SURF)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        self._btn(bar, "📂  Abrir", self.open_dialog, width=84).pack(side="left")
        self._btn(bar, "💾  Guardar", self.save_text, width=96).pack(side="left", padx=5)
        self._btn(bar, "✦  Re-analizar", lambda: self.schedule_analysis(10), width=112).pack(side="left")
        self._btn(bar, "🗑  Borrar todo", self.clear_all, width=112).pack(side="left", padx=5)
        self.btn_more = self._btn(bar, "☰  Más", self.more_menu, width=72)
        self.btn_more.pack(side="left")
        self.btn_math = self._btn(bar, {"auto": "∑ Auto", "on": "∑ Siempre", "off": "∑ No"}[self._math_mode()],
                                  self.cycle_math, width=70)
        self.btn_math.pack(side="left", padx=(5, 0))
        ctk.CTkSwitch(bar, text="Colores", variable=self.v_colors, command=self.recolor,
                      progress_color=CYAN, font=font(12), text_color=MUTED).pack(side="left", padx=12)
        self.lbl_stats = ctk.CTkLabel(bar, text="", font=font(12), text_color=MUTED)
        self.lbl_stats.pack(side="right")

        self.txt = tk.Text(box, wrap="word", bg=SURF, fg=TXT, insertbackground=CYAN,
                           selectbackground="#1d3b5c", relief="flat", borderwidth=0,
                           font=("Segoe UI", 13), padx=22, pady=10, undo=True, spacing1=2,
                           spacing2=4, spacing3=10, highlightthickness=0)
        self.txt.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 10))
        sb = ctk.CTkScrollbar(box, command=self.txt.yview, button_color=SURF3,
                              button_hover_color=CYAN_D)
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 10))
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.tag_configure("title", foreground=CYAN, font=("Bahnschrift", 16, "bold"), spacing3=14)
        self.txt.tag_configure("tagline", foreground="#5d6a8c")
        self.txt.tag_configure("unknown", foreground="#c9d3f0", underline=False)
        self.txt.tag_configure("doubt", underline=True, underlinefg=AMBER)
        self.txt.tag_configure("reading", background="#123049")
        self.txt.tag_raise("reading")
        self.txt.bind("<<Modified>>", self._on_modified)
        self.txt.bind("<Button-3>", self._context_menu)
        for seq in ("<Control-a>", "<Control-A>"):          # en Tk, Ctrl+A iba al inicio de la línea
            self.txt.bind(seq, lambda e: (self.select_all(), "break")[1])
        self.txt.bind("<Control-MouseWheel>", lambda e: (self.zoom_text(1 if e.delta > 0 else -1), "break")[1])
        self._apply_text_size()

        # zona de arrastre
        self.drop = ctk.CTkFrame(box, fg_color=SURF2, corner_radius=18, border_width=2,
                                 border_color=CYAN_D)
        ctk.CTkLabel(self.drop, text="⇣", font=font(54, "bold"), text_color=CYAN).pack(pady=(34, 0))
        ctk.CTkLabel(self.drop, text="Arrastra aquí tu novela", font=font(22, "bold", "Bahnschrift"),
                     text_color=TXT).pack()
        ctk.CTkLabel(self.drop, text=docs.FORMATS + "\nsin límite de palabras · o escribe y pega el texto en el editor",
                     font=font(13), text_color=MUTED).pack(pady=(6, 16))
        row = ctk.CTkFrame(self.drop, fg_color=SURF2)
        row.pack(pady=(0, 30))
        self._btn(row, "Abrir archivo", self.open_dialog, accent=True, width=150).pack(side="left", padx=6)
        self._btn(row, "Escribir / pegar", lambda: (self._show_drop(False), self.txt.focus_set()),
                  width=150).pack(side="left", padx=6)

    def _show_drop(self, show):
        if show:
            self.drop.place(relx=0.5, rely=0.52, anchor="center", relwidth=0.62, relheight=0.56)
            self.drop.lift()
        else:
            self.drop.place_forget()

    def _panel(self, parent):
        holder = ctk.CTkFrame(parent, fg_color=BG, width=456)
        holder.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        holder.pack_propagate(False)
        tv = ctk.CTkTabview(holder, width=456, fg_color=SURF, corner_radius=16, border_width=1,
                            border_color=LINE, segmented_button_fg_color=SURF2,
                            segmented_button_selected_color=CYAN_D,
                            segmented_button_selected_hover_color=CYAN_D,
                            segmented_button_unselected_color=SURF2,
                            segmented_button_unselected_hover_color=SURF3, text_color=TXT)
        tv.pack(fill="both", expand=True)
        self.tabs = tv
        try:
            tv._segmented_button.configure(font=font(11))     # 7 pestañas caben en el panel
        except Exception:
            pass
        for name in ("Reparto", "Voz", "IA", "Música", "Exportar", "Dicción", "Asistente"):
            tv.add(name)
        self._tab_cast(tv.tab("Reparto"))
        self._tab_voice(tv.tab("Voz"))
        self._tab_ai(tv.tab("IA"))
        self._tab_music(tv.tab("Música"))
        self._tab_export(tv.tab("Exportar"))
        self._tab_lexicon(tv.tab("Dicción"))
        self._tab_assistant(tv.tab("Asistente"))

    def _section(self, parent, text, pady=(12, 4)):
        ctk.CTkLabel(parent, text=text.upper(), font=font(11, "bold"), text_color=CYAN).pack(
            anchor="w", padx=4, pady=pady)

    def _combo(self, parent, var, cmd=None, width=380):
        return ctk.CTkComboBox(parent, variable=var, values=[], state="readonly", width=width,
                               fg_color=SURF2, border_color=LINE, button_color=SURF3,
                               button_hover_color=CYAN_D, dropdown_fg_color=SURF2,
                               dropdown_hover_color=CYAN_D, dropdown_text_color=TXT,
                               text_color=TXT, font=font(12), dropdown_font=font(12),
                               command=cmd)

    def _slider(self, parent, label, var, a, b, fmt, steps=None, cmd=None):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=4, pady=(6, 0))
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label, font=font(12), text_color=TXT).pack(side="left")
        val = ctk.CTkLabel(top, text="", font=font(12, "bold"), text_color=CYAN)
        val.pack(side="right")

        def upd(*_):
            try:
                val.configure(text=fmt(var.get()))
            except Exception:
                pass
            if cmd:
                cmd()
        ctk.CTkSlider(f, from_=a, to=b, variable=var, number_of_steps=steps, command=lambda _v: upd(),
                      progress_color=CYAN, button_color=TXT, button_hover_color=CYAN,
                      fg_color=SURF3).pack(fill="x", pady=(2, 0))
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))
        upd()

    def _switch(self, parent, text, var, cmd=None):
        ctk.CTkSwitch(parent, text=text, variable=var, progress_color=CYAN, font=font(12),
                      text_color=TXT, command=cmd).pack(anchor="w", padx=4, pady=4)

    # --- Reparto
    def _tab_cast(self, t):
        nc = ctk.CTkFrame(t, fg_color=SURF2, corner_radius=12, border_width=1, border_color=LINE)
        nc.pack(fill="x", padx=2, pady=(4, 6))
        top = ctk.CTkFrame(nc, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(top, text="🎙  NARRADOR", font=font(13, "bold", "Bahnschrift"),
                     text_color=AMBER).pack(side="left")
        ctk.CTkButton(top, text="▶", width=30, height=26, fg_color=SURF3, hover_color=CYAN_D,
                      command=self.test_narrator).pack(side="right")
        self.cb_narr = self._combo(nc, self.v_narr, lambda _v: self._narr_changed(), width=390)
        self.cb_narr.pack(padx=12, pady=(4, 12))

        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", padx=2)
        ctk.CTkLabel(row, text="PERSONAJES", font=font(11, "bold"), text_color=CYAN).pack(side="left", padx=2)
        self._btn(row, "+ Añadir", self.add_character, width=76).pack(side="right")
        self._btn(row, "↻ Repartir", self.recast_all, width=86).pack(side="right", padx=6)
        self.btn_review = self._btn(row, "🔍 Revisar dudosos", self.review_doubts, width=150)
        self.btn_review.pack(side="right")
        self.lbl_cast_info = ctk.CTkLabel(t, text="Abre un documento: Kuon IA detectará a los personajes "
                                                    "y les dará voz automáticamente.",
                                          font=font(11), text_color=MUTED, wraplength=400, justify="left")
        self.lbl_cast_info.pack(anchor="w", padx=4, pady=(4, 2))
        self.cast_list = ctk.CTkScrollableFrame(t, fg_color=SURF, scrollbar_button_color=SURF3,
                                                scrollbar_button_hover_color=CYAN_D)
        self.cast_list.pack(fill="both", expand=True, pady=(4, 0))

    # --- Voz
    def _tab_voice(self, t):
        sc = ctk.CTkScrollableFrame(t, fg_color=SURF, scrollbar_button_color=SURF3)
        sc.pack(fill="both", expand=True)
        self._section(sc, "Narración", (4, 4))
        self.v_multi = tk.StringVar(value="Multivoz" if self.v_charvoices.get() else "Solo el narrador")
        ctk.CTkSegmentedButton(sc, values=["Multivoz", "Solo el narrador"], variable=self.v_multi,
                               command=lambda v: (self.v_charvoices.set(v == "Multivoz"), self._render_cast()),
                               selected_color=CYAN_D, selected_hover_color=CYAN_D, unselected_color=SURF2,
                               unselected_hover_color=SURF3, font=font(12)).pack(fill="x", padx=4)
        ctk.CTkLabel(sc, text="«Solo el narrador» lee toda la historia, diálogos incluidos, con una sola voz.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4)
        self._section(sc, "Fuente de voces")
        ctk.CTkSegmentedButton(sc, values=["Automático", "Microsoft", "Sin internet"], variable=self.v_vmode,
                               command=self._vmode_changed, selected_color=CYAN_D, selected_hover_color=CYAN_D,
                               unselected_color=SURF2, unselected_hover_color=SURF3, font=font(12)).pack(fill="x",
                                                                                                         padx=4)
        ctk.CTkLabel(sc, text=f"Automático decide al empezar: Microsoft si hay internet, «{audio.OFFLINE_NAME}» "
                              "si no. «Sin internet» usa siempre la voz natural que viene con el programa (la misma "
                              "del teléfono); los personajes se distinguen por el tono.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4)
        ctk.CTkLabel(sc, text="Si se cae internet a mitad de la narración", font=font(12),
                     text_color=TXT).pack(anchor="w", padx=4, pady=(8, 2))
        ctk.CTkSegmentedButton(sc, values=["Esperar a que vuelva", "Seguir con la voz natural"], variable=self.v_drop,
                               command=lambda _v: self._autosave(), selected_color=CYAN_D,
                               selected_hover_color=CYAN_D, unselected_color=SURF2, unselected_hover_color=SURF3,
                               font=font(12)).pack(fill="x", padx=4)
        self._section(sc, "Idioma de la narración")
        self.cb_lang = self._combo(sc, self.v_lang_label, lambda _v: self._lang_changed())
        self.cb_lang.pack(anchor="w", padx=4)
        ctk.CTkLabel(sc, text="Al cambiarlo, Kuon IA elige las mejores voces de ese acento para el narrador "
                              "y los personajes.", font=font(11), text_color=MUTED, wraplength=390,
                     justify="left").pack(anchor="w", padx=4)
        self._section(sc, "Estilo de narración")
        ctk.CTkSegmentedButton(sc, values=list(PRESETS), variable=self.v_preset,
                               command=lambda _v: self._apply_preset(), selected_color=CYAN_D,
                               selected_hover_color=CYAN_D, unselected_color=SURF2,
                               unselected_hover_color=SURF3, font=font(12)).pack(fill="x", padx=4)
        self._slider(sc, "Velocidad", self.v_speed, 0.5, 3.0, lambda v: f"{v:.2f}x", 50,
                     cmd=self._update_stats_light)
        q = ctk.CTkFrame(sc, fg_color="transparent")
        q.pack(fill="x", padx=4, pady=(4, 0))
        for sp in (1.0, 1.25, 1.5, 1.75, 2.0):
            ctk.CTkButton(q, text=f"{sp}x", width=60, height=24, fg_color=SURF3, hover_color=CYAN_D,
                          font=font(11), command=lambda v=sp: self.v_speed.set(v)).pack(side="left", padx=(0, 5))
        self._slider(sc, "Intensidad de las emociones", self.v_emo_int, 0, 1.5, lambda v: f"{int(round(v * 100))} %", 30)
        ctk.CTkLabel(sc, text="Kuon IA lee cada frase y le da el tono que pide: grito, susurro, miedo, ternura, "
                              "duda, enojo… 0 % = lectura plana. 100 % = natural. 150 % = teatral.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4)
        self._slider(sc, "Tono del narrador", self.v_pitch, -40, 40, lambda v: f"{int(round(v)):+d} Hz", 80)
        ctk.CTkButton(sc, text="🎙 Narrador grave (Juan, Costa Rica, tono −14 Hz)", height=28, fg_color=SURF3,
                      hover_color=LINE, font=font(12), command=self.deep_narrator).pack(anchor="w", padx=4, pady=(6, 0))
        self._slider(sc, "Volumen del narrador", self.v_vol, -50, 50, lambda v: f"{int(round(v)):+d} %", 100)
        self._section(sc, "Voces para diálogos sin nombre")
        ctk.CTkLabel(sc, text="Masculina", font=font(12), text_color=MUTED).pack(anchor="w", padx=4)
        self.cb_gm = self._combo(sc, self.v_gm)
        self.cb_gm.pack(anchor="w", padx=4)
        ctk.CTkLabel(sc, text="Femenina", font=font(12), text_color=MUTED).pack(anchor="w", padx=4, pady=(6, 0))
        self.cb_gf = self._combo(sc, self.v_gf)
        self.cb_gf.pack(anchor="w", padx=4)

    # --- IA
    def _tab_ai(self, t):
        sc = ctk.CTkScrollableFrame(t, fg_color=SURF, scrollbar_button_color=SURF3)
        sc.pack(fill="both", expand=True)
        self._section(sc, "Kuon IA (integrada, sin internet)", (4, 4))
        self._switch(sc, "Voz distinta para cada personaje", self.v_charvoices,
                     cmd=lambda: (self.v_multi.set("Multivoz" if self.v_charvoices.get() else "Solo el narrador"),
                                  self._render_cast()))
        self._switch(sc, "Asignar voces automáticamente a personajes nuevos", self.v_autocast)
        self._switch(sc, "Interpretar emociones (grito, susurro, llanto, risa…)", self.v_emotions)
        self._switch(sc, "Ajustar ritmo según la escena (acción, tensión, romance)", self.v_moods)
        self._switch(sc, "Omitir acotaciones cortas (\"dijo Eren\")", self.v_skip)
        self._switch(sc, "No leer notas del autor (N/A, «Nota del autor:»…)", self.v_skipnotes)
        self._switch(sc, "No leer texto entre [corchetes] (ojo: mensajes de «sistema»)", self.v_skipbr)
        ctk.CTkLabel(sc, text="Fórmulas y unidades en español (d²y/dx², 220 V, ω…)", font=font(12),
                     text_color=TXT).pack(anchor="w", padx=4, pady=(8, 2))
        ctk.CTkSegmentedButton(sc, values=list(MATH_LABEL.values()), variable=self.v_math,
                               command=lambda _v: self._math_changed(), selected_color=CYAN_D,
                               selected_hover_color=CYAN_D, unselected_color=SURF2, unselected_hover_color=SURF3,
                               font=font(12)).pack(fill="x", padx=4)
        ctk.CTkLabel(sc, text="Automático: solo las frases que parecen fórmulas se leen como fórmulas (el «·» de "
                              "«Lima · Perú» es una pausa). Siempre: para apuntes de matemáticas. Nunca: novelas.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4)
        self._switch(sc, "Estudio: saltar citas [12], (Autor, 2020) y la sección «Referencias»", self.v_cites)
        self._switch(sc, "Leer títulos de capítulo", self.v_titles)
        self._section(sc, "Cuando Kuon IA duda")
        self._slider(sc, "Seguridad mínima para dar voz de personaje", self.v_threshold, 0.3, 0.95,
                     lambda v: f"{int(v * 100)} %", 65, cmd=lambda: self.schedule_analysis(900))
        ctk.CTkLabel(sc, text="Si no está así de segura de quién habla, ese diálogo lo lee el narrador. "
                              "Más alto = menos errores de voz, más líneas para el narrador.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4)
        self._section(sc, "Lo que Kuon IA ha aprendido")
        self.lbl_memory = ctk.CTkLabel(sc, text="", font=font(12), text_color=TXT, wraplength=390, justify="left")
        self.lbl_memory.pack(anchor="w", padx=4)
        row = ctk.CTkFrame(sc, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=6)
        self._btn(row, "Ver detalle", self.show_memory, width=110).pack(side="left")
        self._btn(row, "Olvidar todo", self.forget_memory, width=110).pack(side="left", padx=6)
        self._update_memory_label()
        row = ctk.CTkFrame(sc, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(2, 6))
        self._btn(row, "Tutorial", self.start_tour, width=90).pack(side="left", padx=(0, 6))
        self._btn(row, "Novedades", self.show_news, width=110).pack(side="left")
        self._btn(row, "Registro de errores", self.show_log, width=150).pack(side="left", padx=6)
        self._section(sc, "Pausas naturales")
        self._slider(sc, "Entre diálogo y acotación", self.v_p_inner, 0, 600, lambda v: f"{int(v)} ms", 60)
        self._slider(sc, "Entre párrafos", self.v_p_para, 100, 1500, lambda v: f"{int(v)} ms", 70)
        self._slider(sc, "Cambio de escena / capítulo", self.v_p_scene, 300, 4000, lambda v: f"{int(v)} ms", 74)
        self._section(sc, "Refuerzo con IA local (Ollama, opcional)")
        self.lbl_ollama = ctk.CTkLabel(sc, text="Buscando Ollama…", font=font(12), text_color=MUTED,
                                       wraplength=390, justify="left")
        self.lbl_ollama.pack(anchor="w", padx=4)
        self.cb_model = self._combo(sc, self.v_model)
        self.cb_model.pack(anchor="w", padx=4, pady=6)
        self.btn_ollama = self._btn(sc, "✦  Revisar reparto con IA local", self.run_ollama, width=260)
        self.btn_ollama.pack(anchor="w", padx=4)
        ctk.CTkLabel(sc, text="Lee los diálogos dudosos con un modelo de lenguaje en tu PC y corrige "
                              "quién habla, además del género y la edad de cada personaje. "
                              "Tus correcciones manuales (clic derecho en un diálogo) siempre mandan.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4, pady=6)

    # --- Música
    def _tab_music(self, t):
        self._section(t, "Música de fondo", (4, 4))
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", padx=4)
        ctk.CTkEntry(row, textvariable=self.v_music, fg_color=SURF2, border_color=LINE,
                     placeholder_text="Sin música").pack(side="left", fill="x", expand=True)
        self._btn(row, "Elegir…", self.pick_music, width=70).pack(side="left", padx=4)
        self._btn(row, "✕", lambda: self.v_music.set(""), width=34).pack(side="left")
        self._slider(t, "Volumen de la música", self.v_music_db, -40, -8, lambda v: f"{v:.0f} dB", 32)
        self._switch(t, "Bajar la música cuando alguien habla (ducking)", self.v_duck)
        ctk.CTkLabel(t, text="MP3, WAV, FLAC u OGG. La pista se repite en bucle, entra con un fundido y "
                             "se apaga al final. Usa música sin copyright para YouTube.",
                     font=font(11), text_color=MUTED, wraplength=390, justify="left").pack(anchor="w", padx=4, pady=8)

    # --- Exportar
    def _tab_export(self, t):
        sc = ctk.CTkScrollableFrame(t, fg_color=SURF, scrollbar_button_color=SURF3)
        sc.pack(fill="both", expand=True)
        self._section(sc, "Destino", (4, 4))
        row = ctk.CTkFrame(sc, fg_color="transparent")
        row.pack(fill="x", padx=4)
        ctk.CTkEntry(row, textvariable=self.v_outdir, fg_color=SURF2, border_color=LINE).pack(
            side="left", fill="x", expand=True)
        self._btn(row, "…", self.pick_outdir, width=34).pack(side="left", padx=4)
        self._btn(row, "Abrir", self.open_outdir, width=60).pack(side="left")
        self._section(sc, "Varios documentos a la vez")
        ctk.CTkLabel(sc, text="Elige varios capítulos o bloques y se genera un audio por cada uno, con el mismo "
                              "nombre, en la carpeta de destino. Usa tus voces y ajustes actuales.",
                     font=font(12), text_color=MUTED, wraplength=330, justify="left").pack(anchor="w", padx=4)
        self._btn(sc, "📚  Convertir varios documentos…", self.batch_convert, width=260).pack(anchor="w", padx=4,
                                                                                             pady=(6, 0))
        self._btn(sc, "🎬  Subtitular mi video (sin internet)…", self.subs_window, width=260).pack(anchor="w", padx=4,
                                                                                                  pady=(6, 0))
        self._section(sc, "Formato")
        row = ctk.CTkFrame(sc, fg_color="transparent")
        row.pack(fill="x", padx=4)
        ctk.CTkSegmentedButton(row, values=["MP3", "WAV"], variable=self.v_fmt, selected_color=CYAN_D,
                               unselected_color=SURF2, font=font(12)).pack(side="left")
        ctk.CTkLabel(row, text="  kbps", font=font(12), text_color=MUTED).pack(side="right")
        ctk.CTkOptionMenu(row, values=["96", "128", "192", "256", "320"], variable=self.v_bitrate,
                          width=80, fg_color=SURF3, button_color=SURF3, button_hover_color=CYAN_D,
                          dropdown_fg_color=SURF2).pack(side="right")
        self._switch(sc, "Un archivo por capítulo", self.v_split)
        self._switch(sc, "Subtítulos .srt sincronizados", self.v_srt)
        self._switch(sc, "Descripción de YouTube con capítulos (.txt)", self.v_yt)
        self._switch(sc, "Video MP4 para YouTube (fondo animado, subtítulos y onda de voz)", self.v_mp4)
        brow = ctk.CTkFrame(sc, fg_color="transparent")
        brow.pack(fill="x", padx=4, pady=(2, 2))
        ctk.CTkLabel(brow, text="Fondo", font=font(12), text_color=MUTED).pack(side="left")
        self._ia_bgmenu = ctk.CTkOptionMenu(brow, values=list(video.BACKGROUNDS) + ["Mi portada (zoom lento)"],
                                            variable=self.v_vbg, width=200, fg_color=SURF3, button_color=SURF3,
                                            button_hover_color=CYAN_D, dropdown_fg_color=SURF2,
                                            command=lambda _v: self._autosave())
        self._ia_bgmenu.pack(side="left", padx=8)
        self._ia_brow = brow
        # fila de estilo y modelo de las imágenes con IA (solo si esta PC tiene el motor)
        self._ia_arow = ctk.CTkFrame(sc, fg_color="transparent")
        ctk.CTkLabel(self._ia_arow, text="IA", font=font(12), text_color=MUTED).pack(side="left")
        # por defecto todo automático (el estilo y el modelo salen del prompt, como en el Estudio de Imágenes);
        # si eliges algo a mano, se respeta
        ctk.CTkOptionMenu(self._ia_arow, values=list(imagen_ia.STYLES), variable=self.v_vstyle, width=150,
                          fg_color=SURF3, button_color=SURF3, button_hover_color=CYAN_D, dropdown_fg_color=SURF2,
                          command=lambda _v: self._autosave()).pack(side="left", padx=(8, 0))
        self._ia_engmenu = ctk.CTkOptionMenu(self._ia_arow, values=[imagen_ia.AUTO], variable=self.v_vqual, width=215,
                                             fg_color=SURF3, button_color=SURF3, button_hover_color=CYAN_D,
                                             dropdown_fg_color=SURF2, command=lambda _v: self._autosave())
        self._ia_engmenu.pack(side="left", padx=(8, 0))
        self._btn(self._ia_arow, "✍ Ejemplos", self.open_prompt_examples, width=90).pack(side="left", padx=(8, 0))
        self.v_thumbauto = tk.BooleanVar(value=self.cfg.get("auto_thumb", False))
        ctk.CTkSwitch(sc, text="🎬 Miniatura automática (título de la obra + capítulo, como tus ejemplos)",
                      variable=self.v_thumbauto, progress_color=CYAN_D, font=font(12),
                      command=self._autosave).pack(anchor="w", padx=4, pady=(4, 2))
        # si no lo tiene: botón que explica qué se descarga, cuánto pesa, y lo instala
        self._ia_setup = self._btn(sc, "✨ Activar imágenes con IA (portadas y fondos por capítulo)…",
                                   self.open_ia_setup)
        crow = ctk.CTkFrame(sc, fg_color="transparent")
        crow.pack(fill="x", padx=4)
        ctk.CTkEntry(crow, textvariable=self.v_cover, placeholder_text="Portada (para «Mi portada»)",
                     fg_color=SURF2, border_color=LINE).pack(side="left", fill="x", expand=True)
        self._btn(crow, "…", self.pick_cover, width=34).pack(side="left", padx=4)
        self._btn(crow, "IA", self.open_image_studio, width=34).pack(side="left", padx=(0, 4))
        self._btn(crow, "✕", lambda: self.v_cover.set(""), width=30).pack(side="left")
        self._ia_refresh()
        self._switch(sc, "Subtítulos dentro del video", self.v_vsubs)
        self._switch(sc, "Nombre del personaje que habla (con su color)", self.v_vnames)
        self._switch(sc, "Vertical 9:16 (Shorts, TikTok, Reels)", self.v_vvert)
        self._slider(sc, "Fragmentos en paralelo", self.v_workers, 1, 8, lambda v: f"{int(v)}", 7)
        self._section(sc, "Capítulos detectados")
        self.ch_list = ctk.CTkFrame(sc, fg_color="transparent")
        self.ch_list.pack(fill="x", padx=2)

    # --- Dicción
    def _tab_lexicon(self, t):
        self._section(t, "Palabras que pueden sonar raro", (4, 2))
        self.lbl_foreign = ctk.CTkLabel(t, text="Abre un documento: Kuon IA buscará nombres en inglés o "
                                                "inventados (Lovegod, Nightwolf…).",
                                        font=font(11), text_color=MUTED, wraplength=400, justify="left")
        self.lbl_foreign.pack(anchor="w", padx=4)
        self.foreign_list = ctk.CTkScrollableFrame(t, fg_color=SURF, height=250, scrollbar_button_color=SURF3,
                                                   scrollbar_button_hover_color=CYAN_D)
        self.foreign_list.pack(fill="both", expand=True, pady=(4, 2))
        self._section(t, "Diccionario propio  ·  Palabra = cómo suena", (8, 2))
        self.lex = ctk.CTkTextbox(t, fg_color=SURF2, text_color=TXT, font=("Consolas", 13), height=120,
                                  border_width=1, border_color=LINE)
        self.lex.pack(fill="x", padx=2)
        self.lex.insert("1.0", self.cfg.get("lexicon", docs.DEFAULT_LEXICON))
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", pady=6)
        self._btn(row, "▶ Probar selección", self.test_lexicon, width=150).pack(side="left", padx=2)
        self._section(t, "Paquete por fandom (DxD, One Piece…)", (8, 2))
        ctk.CTkLabel(t, text="Guarda la dicción de esta obra y tu diccionario en un archivo .kuon.json para usarlo en "
                             "otras obras de la misma saga, compartirlo o pasarlo al teléfono.",
                     font=font(11), text_color=MUTED, wraplength=400, justify="left").pack(anchor="w", padx=4)
        prow = ctk.CTkFrame(t, fg_color="transparent")
        prow.pack(fill="x", pady=6)
        self._btn(prow, "💾 Guardar paquete…", self.export_pron_pack, width=150).pack(side="left", padx=2)
        self._btn(prow, "📥 Cargar paquete…", self.import_pron_pack, width=150).pack(side="left", padx=6)

    # --- paquetes de dicción (idea de @Voice: listas de pronunciación que se comparten)
    def export_pron_pack(self):
        lex = self.lex.get("1.0", "end").strip()
        if not self.pron and not lex:
            self.status("No hay dicción ni diccionario que guardar todavía.")
            return
        name = self.project if self.project != "_sin_titulo" else "mi_diccion"
        p = filedialog.asksaveasfilename(title="Guardar paquete de dicción", defaultextension=".kuon.json",
                                         initialfile=self._safe_name(name) + ".kuon.json",
                                         filetypes=[("Paquete de dicción", "*.kuon.json"), ("JSON", "*.json")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump(pron_pack(name, self.pron, lex), f, ensure_ascii=False, indent=1)
        self.status(f"Paquete guardado: {os.path.basename(p)} ({len(self.pron)} palabras)")

    def import_pron_pack(self, path=None):
        p = path or filedialog.askopenfilename(title="Cargar paquete de dicción",
                                               filetypes=[("Paquete de dicción", "*.kuon.json *.json")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            added, lines = merge_pron_pack(d, self.pron, self.lex.get("1.0", "end"))
        except Exception as e:
            messagebox.showerror(APP, f"Ese archivo no es un paquete de dicción válido.\n\n{e}")
            return
        self.lex.delete("1.0", "end")
        self.lex.insert("1.0", lines)
        self._render_foreign()
        self._autosave()
        self.schedule_plan(200)
        self.status(f"Paquete «{d.get('nombre', '')}» cargado: {added} palabras nuevas para esta obra.")

    PRON_LABELS ={"es": "Como se escribe", "en": "En inglés", "raw": "Sin tocar"}

    def _set_foreign(self, found):
        self.foreign = found or []
        self._foreign_limit = 60
        docs.auto_pron(self.foreign, self.pron)
        self._render_foreign()

    def _render_foreign(self):
        for w in self.foreign_list.winfo_children():
            w.destroy()
        if not self.foreign:
            self.lbl_foreign.configure(text="No hay palabras dudosas en este documento.")
            return
        self.lbl_foreign.configure(text=f"{len(self.foreign)} palabras. Elige cómo las dice la voz y pruébalas con ▶. "
                                        "Se guarda para todo el proyecto.")
        labels = list(self.PRON_LABELS.values())
        back = {v: k for k, v in self.PRON_LABELS.items()}
        lim = min(getattr(self, "_foreign_limit", 60), 300)
        for d in self.foreign[:lim]:
            w = d["word"]
            card = ctk.CTkFrame(self.foreign_list, fg_color=SURF2, corner_radius=10, border_width=1,
                                border_color=LINE)
            card.pack(fill="x", pady=3, padx=2)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 0))
            ctk.CTkLabel(top, text=w, font=font(13, "bold"), text_color=TXT).pack(side="left")
            ctk.CTkLabel(top, text=f"  ×{d['count']}", font=font(11), text_color=MUTED).pack(side="left")
            hint = ctk.CTkLabel(top, text="", font=font(11, "normal", "Consolas"), text_color=CYAN)
            ctk.CTkButton(top, text="▶", width=28, height=24, fg_color=SURF3, hover_color=CYAN_D,
                          command=lambda w=w: self.test_word(w)).pack(side="right")
            hint.pack(side="right", padx=6)
            mode = self.pron.get(w, "raw")
            var = tk.StringVar(value=self.PRON_LABELS.get(mode, "Sin tocar"))

            def show(m, d=d, hint=hint):
                hint.configure(text="→ " + (d["es"] if m == "es" else d["en"] if m == "en" else d["word"]))
            show(mode)

            def change(v, w=w, show=show):
                m = back.get(v, "raw")
                self._checkpoint(f"pronunciación de {w}")
                self.pron[w] = m
                show(m)
            ctk.CTkSegmentedButton(card, values=labels, variable=var, command=change, selected_color=CYAN_D,
                                   selected_hover_color=CYAN_D, unselected_color=SURF3,
                                   unselected_hover_color=LINE, font=font(11), height=24).pack(
                fill="x", padx=10, pady=(4, 8))
        if len(self.foreign) > lim:
            more = len(self.foreign) - lim
            if lim < 300:
                ctk.CTkButton(self.foreign_list, text=f"Mostrar más ({more} restantes)", fg_color=SURF3, hover_color=LINE,
                              command=lambda: (setattr(self, "_foreign_limit", lim + 60), self._render_foreign())
                              ).pack(fill="x", pady=6)
            else:
                ctk.CTkLabel(self.foreign_list, text=f"Y {more} palabras más: escríbelas en tu diccionario propio "
                                                     "(Palabra = cómo suena).", font=font(11), text_color=MUTED,
                             wraplength=360).pack(pady=6)

    def test_word(self, w):
        st = self._settings()
        line = None
        for s in self.segs:
            if w in s.text and len(s.text) < 200:
                line = docs.clean_for_speech(s.text)
                break
        text = docs.apply_lexicon(docs.apply_pron(line or w, docs.pron_rules(st.pron)), docs.parse_lexicon(st.lexicon))
        self._test(audio.Chunk(text, st.narrator, round((st.speed - 1) * 100), st.pitch, 1.0, 200, 0, 0))

    # --- Reproductor
    def _player(self):
        p = ctk.CTkFrame(self, fg_color=SURF, corner_radius=16, border_width=1, border_color=LINE, height=92)
        p.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        p.grid_columnconfigure(2, weight=1)
        self.btn_play = ctk.CTkButton(p, text="▶", width=58, height=58, corner_radius=29, fg_color=CYAN,
                                      hover_color="#67e8f9", text_color="#04121a", font=font(24, "bold"),
                                      command=self.play_toggle)
        self.btn_play.grid(row=0, column=0, rowspan=4, padx=(16, 8), pady=14)
        side = ctk.CTkFrame(p, fg_color="transparent")
        side.grid(row=0, column=1, rowspan=4, padx=(0, 12))
        nav = ctk.CTkFrame(side, fg_color="transparent")
        nav.pack(pady=(0, 4))
        ctk.CTkButton(nav, text="⏮", width=30, height=30, corner_radius=15, fg_color=SURF3, hover_color=LINE,
                      font=font(12), command=lambda: self.jump_para(-1)).pack(side="left", padx=1)
        ctk.CTkButton(nav, text="■", width=34, height=30, corner_radius=15, fg_color=SURF3,
                      hover_color=LINE, font=font(14), command=self.stop_all).pack(side="left", padx=1)
        ctk.CTkButton(nav, text="⏭", width=30, height=30, corner_radius=15, fg_color=SURF3, hover_color=LINE,
                      font=font(12), command=lambda: self.jump_para(1)).pack(side="left", padx=1)
        ctk.CTkButton(side, text="Todo", width=40, height=24, corner_radius=12, fg_color=SURF3,
                      hover_color=CYAN_D, font=font(10), command=self.preview_all).pack()
        self.btn_chg = ctk.CTkButton(side, text="Cambios", width=40, height=22, corner_radius=11, fg_color=AMBER,
                                     hover_color="#fbbf24", text_color="#120a00", font=font(10, "bold"),
                                     command=self.play_changes)
        info = ctk.CTkFrame(p, fg_color="transparent")
        info.grid(row=0, column=2, sticky="ew", pady=(12, 0))
        self.lbl_now = ctk.CTkLabel(info, text="▶ desde el cursor (o donde quedaste) · «Todo» desde el inicio",
                                    font=font(12), text_color=MUTED)
        self.lbl_now.pack(side="left")
        self.lbl_time = ctk.CTkLabel(info, text="", font=font(12, "bold", "Consolas"), text_color=CYAN)
        self.lbl_time.pack(side="right", padx=8)
        self.viz = tk.Canvas(p, height=46, bg=SURF, highlightthickness=0)
        self.viz.grid(row=1, column=2, sticky="ew", pady=(2, 2))
        self.tl = tk.Canvas(p, height=26, bg=SURF, highlightthickness=0, cursor="hand2")
        self.tl.grid(row=2, column=2, sticky="ew", pady=(0, 4))
        self.lbl_tl = ctk.CTkLabel(p, text="", font=font(10, "normal", "Consolas"), text_color=MUTED, height=14)
        self.lbl_tl.grid(row=3, column=2, sticky="e", pady=(0, 8))
        self.tl.bind("<Button-1>", self._tl_hover)
        self.tl.bind("<B1-Motion>", self._tl_hover)
        self.tl.bind("<ButtonRelease-1>", self._tl_release)
        self.tl.bind("<Configure>", lambda _e: self._tl_later())
        right = ctk.CTkFrame(p, fg_color="transparent")
        right.grid(row=0, column=3, rowspan=4, padx=16, pady=10)
        self.btn_gen = ctk.CTkButton(right, text="⚡  GENERAR AUDIO FINAL", height=40, width=230,
                                     corner_radius=12, fg_color=VIOLET, hover_color="#c4b5fd",
                                     text_color="#120a24", font=font(14, "bold", "Bahnschrift"),
                                     command=self.generate)
        self.btn_gen.pack()
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.pb = ctk.CTkProgressBar(row, progress_color=CYAN, fg_color=SURF3, height=8, width=150)
        self.pb.set(0)
        self.pb.pack(side="left")
        self.btn_cancel = ctk.CTkButton(row, text="Cancelar", width=70, height=22, fg_color=SURF3,
                                        hover_color=RED, font=font(11), command=self.cancel_job,
                                        state="disabled")
        self.btn_cancel.pack(side="right")
        self.lbl_status = ctk.CTkLabel(right, text="Listo", font=font(11), text_color=MUTED, wraplength=240,
                                       justify="left")
        self.lbl_status.pack(anchor="w")
        self._viz_phase = 0.0
        self._spec_s = [0.0] * audio.SPEC_BANDS      # espectro suavizado
        self._spec_peak = [0.0] * audio.SPEC_BANDS   # picos que caen despacio
        self._spec_col = CYAN

    # ------------------------------------------------------------ config
    def _apply_cfg(self):
        self._refresh_langs()
        self._refresh_voice_lists()

    def _settings(self):
        return audio.Settings(
            narrator=self._vname(self.v_narr.get()), speed=round(self.v_speed.get(), 2),
            pitch=int(round(self.v_pitch.get())), volume=int(round(self.v_vol.get())),
            character_voices=self.v_charvoices.get(), scene_moods=self.v_moods.get(),
            emotions=self.v_emotions.get(), skip_tags=self.v_skip.get(), read_titles=self.v_titles.get(),
            skip_notes=self.v_skipnotes.get(), skip_brackets=self.v_skipbr.get(),
            math_mode=self._math_mode(), skip_citations=self.v_cites.get(),
            generic_male=self._vname(self.v_gm.get()), generic_female=self._vname(self.v_gf.get()),
            cast=json.loads(json.dumps(self.cast)), lexicon=self.lex.get("1.0", "end"),
            pause_inner=int(self.v_p_inner.get()), pause_para=int(self.v_p_para.get()),
            pause_title=int(self.v_p_scene.get() * 0.75), pause_scene=int(self.v_p_scene.get()),
            music_path=self.v_music.get().strip(), music_db=float(self.v_music_db.get()),
            duck=self.v_duck.get(), bitrate=int(self.v_bitrate.get() or 128),
            emotion_intensity=round(float(self.v_emo_int.get()), 2), pron=dict(self.pron),
            emo_over=dict(self.emo_over), voice_mode=self._vmode(),
            on_drop="switch" if self.v_drop.get().startswith("Seguir") else "wait",
        )

    def _math_mode(self):
        return {v: k for k, v in MATH_LABEL.items()}.get(self.v_math.get(), "auto")

    def _math_changed(self):
        m = self._math_mode()
        if hasattr(self, "btn_math"):
            self.btn_math.configure(text={"auto": "∑ Auto", "on": "∑ Siempre", "off": "∑ No"}[m])
        self._autosave()
        self.status({"auto": "Fórmulas: automático (solo las frases que parecen fórmulas).",
                     "on": "Fórmulas: siempre (todo el texto se lee como matemática).",
                     "off": "Fórmulas: nunca (se lee como texto normal)."}[m])

    def cycle_math(self):
        order = ["auto", "on", "off"]
        self.v_math.set(MATH_LABEL[order[(order.index(self._math_mode()) + 1) % 3]])
        self._math_changed()

    def _store(self):
        st = self._settings()
        self.cfg.update(
            lang=self.v_lang.get(), narrator=st.narrator, speed=st.speed, pitch=st.pitch, volume=st.volume,
            preset=self.v_preset.get(), character_voices=st.character_voices, autocast=self.v_autocast.get(),
            emotions=st.emotions, scene_moods=st.scene_moods, skip_tags=st.skip_tags,
            skip_notes=st.skip_notes, skip_brackets=st.skip_brackets, math_mode=st.math_mode,
            skip_citations=st.skip_citations,
            read_titles=st.read_titles, colors=self.v_colors.get(), pause_inner=st.pause_inner,
            pause_para=st.pause_para, pause_scene=st.pause_scene, music_path=st.music_path,
            music_db=st.music_db, duck=st.duck, outdir=self.v_outdir.get(), fmt=self.v_fmt.get(),
            bitrate=st.bitrate, split=self.v_split.get(), srt=self.v_srt.get(), yt=self.v_yt.get(),
            mp4=self.v_mp4.get(), cover=self.v_cover.get(), video_bg=self.v_vbg.get(), video_subs=self.v_vsubs.get(),
            video_ai_style=self.v_vstyle.get(), video_ai_engine=self.v_vqual.get(),
            video_names=self.v_vnames.get(), video_vertical=self.v_vvert.get(),
            auto_thumb=bool(getattr(self, "v_thumbauto", None) and self.v_thumbauto.get()), video_auto_v2=True,
            workers=int(self.v_workers.get()), ollama_model=self.v_model.get(),
            threshold=round(self.v_threshold.get(), 2),
            emotion_intensity=st.emotion_intensity, variant=self.v_variant.get(), schema=2,
            cps=round(self.cfg.get("cps", 14.5), 2),
            generic_male=st.generic_male, generic_female=st.generic_female,
            lexicon=st.lexicon, geometry=self.geometry(), voice_mode=st.voice_mode, on_drop=st.on_drop,
        )
        docs_state = self.cfg.setdefault("docs", {})
        docs_state[self.doc_key] = {"overrides": self.overrides, "emo": self.emo_over,
                                    "heard": sorted(self.heard)[-60000:],
                                    "pos": int(getattr(self, "_resume_pos", 0) or 0),
                                    "marks": list(getattr(self, "marks", []))[-500:]}
        projects = self.cfg.setdefault("projects", {})
        projects[self.project] = {"cast": self.cast, "aliases": self.aliases, "pron": self.pron,
                                  "colors": self.char_color}
        save_memory(self.memory)
        save_cfg(self.cfg)

    # ------------------------------------------------------------ voces
    def _load_voices(self):
        vs, ok = vx.load_voices()
        self.after(0, lambda: self._voices_ready(vs, ok))

    def _voices_ready(self, vs, ok):
        self.voices, self.voices_online = vs, ok
        self._net_chip()
        self._refresh_langs()
        self._refresh_voice_lists()
        self._render_cast()
        self._fix_missing_voices()

    def _vmode(self):
        return {"Microsoft": "online", "Sin internet": "offline"}.get(self.v_vmode.get(), "auto")

    def _net_chip(self):
        """Chip de arriba: qué voces se van a usar (como «● Microsoft / ● Teléfono» de la APK)."""
        mode, ok = self._vmode(), getattr(self, "voices_online", False)
        if mode == "offline" or (mode == "auto" and not ok and audio.offline_available()):
            self.chip_net.configure(text="● Sin internet · voz natural", text_color=AMBER)
        elif ok:
            self.chip_net.configure(text=f"● {len(self.voices)} voces en línea", text_color=CYAN)
        else:
            self.chip_net.configure(text="● Sin conexión con Microsoft", text_color=RED)

    def _vmode_changed(self, _v=None):
        self._net_chip()
        if self._vmode() == "offline" and not audio.offline_available():
            messagebox.showwarning(APP, "No se encontró la voz natural (carpeta «voz» junto al programa). "
                                        "Reinstala Audio Novela Studio para recuperarla.")
        self.schedule_plan(200)
        self._autosave()

    def _fix_missing_voices(self):
        """Si Microsoft retira una voz que un personaje tenía asignada, sus diálogos saldrían en silencio:
        se cambia por la voz genérica de su género y se avisa."""
        if not self.voices_online:
            return
        names = {v.name for v in self.voices}
        gone = []
        for nm, c in self.cast.items():
            if c.get("voice") and c["voice"] not in names:
                gone.append(f"{nm} ({c['voice']})")
                c["voice"] = self._vname(self.v_gf.get() if c.get("gender") == "F" else self.v_gm.get())
        if gone:
            self._log_error(RuntimeError("voces que ya no existen: " + ", ".join(gone)))
            self._render_cast()
            self.schedule_plan(300)
            messagebox.showwarning(APP, "Microsoft ya no ofrece algunas voces que tenías asignadas. "
                                        "Se cambiaron por la voz genérica:\n\n" + "\n".join(gone[:12]) +
                                   ("\n…" if len(gone) > 12 else "") + "\n\nPuedes elegir otra en Reparto.")

    def _vname(self, label):
        for v in self.voices:
            if v.label == label:
                return v.name
        return label

    def _vlabel(self, name):
        for v in self.voices:
            if v.name == name:
                return v.label
        return name

    def _refresh_langs(self):
        vars_ = vx.variants_for(self.voices)
        cur = self.v_variant.get()
        if not any(v[0] == cur for v in vars_):
            vars_.insert(0, vx.variant_info(cur))
        self._variant_by_label = {v[1]: v[0] for v in vars_}
        self.cb_lang.configure(values=[v[1] for v in vars_])
        self.v_lang_label.set(vx.variant_info(cur)[1])

    def _lang_changed(self):
        vid = getattr(self, "_variant_by_label", {}).get(self.v_lang_label.get(), "es-419")
        self.v_variant.set(vid)
        self.v_lang.set(vx.variant_info(vid)[2])
        self._refresh_voice_lists(reset=True)
        self.cast = {n: c for n, c in self.cast.items() if not c.get("auto")}
        nv = self._vname(self.v_narr.get()).split("-", 2)[-1].replace("Neural", "")
        npool = sum(len(vx.ordered_pool(self.voices, self.v_lang.get(), g, vid)) for g in ("Male", "Female"))
        few = (f" Este acento tiene {npool} voces: algunos personajes comparten voz con otro tono."
               if self.chars and npool < len(self.chars) + 1 else "")
        self.status(f"{vx.variant_info(vid)[1]}: narra {nv} y los personajes reciben las mejores voces de ese acento.{few}")
        if self.chars:
            self._autocast()
            self._render_cast()

    def _lang_voices(self):
        vs = vx.for_lang(self.voices, self.v_lang.get())
        return sorted(vs, key=lambda v: (v.multilingual, v.gender != "Male", v.locale, v.name))

    def _refresh_voice_lists(self, reset=False):
        vs = self._lang_voices()
        labels = [v.label for v in vs]
        names = [v.name for v in vs]
        for cb in (self.cb_narr, self.cb_gm, self.cb_gf):
            cb.configure(values=labels)

        def pick(var, want, gender):
            cur = self._vname(var.get())
            if not reset and cur in names:
                var.set(self._vlabel(cur))
                return
            if want in names and not reset:
                var.set(self._vlabel(want))
                return
            pool = vx.ordered_pool(self.voices, self.v_lang.get(), gender, self.v_variant.get()) or vs
            if pool:
                var.set(pool[0].label)
        pick(self.v_narr, self.cfg.get("narrator", "es-MX-JorgeNeural"), "Male")
        pick(self.v_gm, self.cfg.get("generic_male", "es-US-AlonsoNeural"), "Male")
        pick(self.v_gf, self.cfg.get("generic_female", "es-MX-DaliaNeural"), "Female")
        if self.v_gm.get() == self.v_narr.get():
            pool = vx.ordered_pool(self.voices, self.v_lang.get(), "Male", self.v_variant.get())
            if len(pool) > 1:
                self.v_gm.set(pool[1].label)

    def deep_narrator(self):
        """Voz grave para narrar: Juan (es-CR) con el tono más bajo. Solo cambia al narrador, no a los personajes."""
        lab = self._vlabel("es-CR-JuanNeural")
        if lab:
            self.v_narr.set(lab)
            self._narr_changed()
        self.v_pitch.set(-14)
        self._autosave()
        self.status("Narrador grave: Juan (Costa Rica), tono −14 Hz. Lo puedes afinar con «Tono del narrador»."
                    if lab else "Tono del narrador: −14 Hz (la voz de Juan aparece cuando se cargan las voces en línea).")

    def _narr_changed(self):
        narr = self._vname(self.v_narr.get())
        for nm, c in self.cast.items():
            if c["voice"] == narr and c.get("auto"):
                c.pop("voice")
        broken = [n for n, c in self.cast.items() if "voice" not in c]
        for n in broken:
            self.cast.pop(n)
        if broken:
            self._autocast()
        self._render_cast()

    def _apply_preset(self):
        p = PRESETS.get(self.v_preset.get())
        if p:
            self.v_speed.set(p["speed"])
            self.v_pitch.set(p["pitch"])
            self.v_vol.set(p["volume"])
            self.v_emotions.set(p["expressive"])
            self.v_moods.set(p["expressive"])
            self.v_emo_int.set(p.get("emo", 1.0))
            self._update_stats_light()
            if self.v_preset.get() == "Clásico":
                self.status("Estilo Clásico: lectura clara y pareja, con emociones suaves.")

    # ------------------------------------------------------------ IA local
    def _check_ollama(self):
        ms = localai.models()
        self.after(0, lambda: self._ollama_ready(ms))

    def _ollama_ready(self, ms):
        self.ollama_models = ms
        if ms:
            self.cb_model.configure(values=ms)
            if self.v_model.get() not in ms:
                pref = [m for m in ms if any(k in m for k in ("qwen", "llama3", "gemma", "mistral"))]
                self.v_model.set((pref or ms)[0])
            self.lbl_ollama.configure(text=f"Ollama detectado con {len(ms)} modelo(s).", text_color=GREEN)
            self.btn_ollama.configure(state="normal")       # si se abrió Ollama después de la app
            self.chip_ai.configure(text="◉ Kuon IA + Ollama")
        elif ms is not None:
            self.lbl_ollama.configure(text="Ollama está abierto pero no tiene modelos. Instala uno, por "
                                           "ejemplo:  ollama pull qwen2.5:7b", text_color=AMBER)
        else:
            self.lbl_ollama.configure(text="Ollama no está abierto. Kuon IA funciona igual; "
                                           "Ollama solo añade una segunda revisión.", text_color=MUTED)
            self.btn_ollama.configure(state="disabled")
            self.after(30000, lambda: threading.Thread(target=self._check_ollama, daemon=True).start())

    def run_ollama(self):
        if self.busy or not self.ollama_models or not self.segs:
            return
        model = self.v_model.get()
        text = self.get_text()
        segs = list(self.segs)
        names = list(self.chars)
        self._start_job(f"IA local ({model}) revisando el reparto…")

        def work():
            try:
                prof = localai.profile(model, text, names[:40])
                ov = localai.attribute(model, text, segs, names, progress=self._progress, cancel=self.cancel)
                self.after(0, lambda: self._ollama_done(prof, ov))
            except Exception as e:
                self.after(0, lambda e=e: self._job_error(e))
        threading.Thread(target=work, daemon=True).start()

    def _ollama_done(self, prof, ov):
        self._end_job()
        changed = 0
        for nm, p in prof.items():
            c = self.cast.get(nm)
            if not c:
                continue
            if p.get("gender") and p["gender"] != c.get("gender") and c.get("auto"):
                self.cast.pop(nm)
                changed += 1
            elif p.get("age") and c.get("auto"):
                c["age"] = p["age"]
        for k, v in ov.items():
            if self.overrides.get(k) is None:
                self.overrides[k] = v
        self.status(f"IA local: {len(ov)} diálogos revisados, {changed} personajes corregidos.")
        self._gender_override = {nm: p["gender"] for nm, p in prof.items() if p.get("gender")}
        self.schedule_analysis(10)

    # ------------------------------------------------------------ documento
    def _bind_dnd(self):
        if not DND_OK:
            return
        for w in (self, self.txt, self.drop):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
                w.dnd_bind("<<DropEnter>>", lambda e: (self.drop.configure(border_color=CYAN), e.action)[1])
                w.dnd_bind("<<DropLeave>>", lambda e: (self.drop.configure(border_color=CYAN_D), e.action)[1])
            except Exception:
                pass

    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        for p in paths:
            p = p.strip("{}")
            ext = os.path.splitext(p)[1].lower()
            if ext in (".mp3", ".wav", ".ogg", ".flac"):
                self.v_music.set(p)
                self.tabs.set("Música")
                self.status("Música de fondo cargada.")
                return event.action
            if os.path.isfile(p):
                self.load_file(p)
                return event.action
        messagebox.showinfo(APP, "Arrastra un documento (" + docs.FORMATS + ") o un audio para la música de fondo.")
        return event.action

    def open_dialog(self):
        p = filedialog.askopenfilename(title="Abrir documento",
                                       filetypes=[("Documentos", " ".join("*" + e for e in docs.SUPPORTED)),
                                                  ("Fotos o escaneos (OCR)", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"),
                                                  ("Todos", "*.*")])
        if p:
            self.load_file(p)

    def load_file(self, path, text=None):
        if text is None:
            try:
                text = docs.read_document(path)
            except docs.NeedsOCR as e:
                self._ocr_then_load(path, e.kind)        # PDF escaneado o foto: se ofrece el OCR
                return
            except Exception as e:
                messagebox.showerror(APP, f"No se pudo leer el documento:\n{e}")
                return
        self._store()
        self.stop_all()
        self.doc_path = path
        self.doc_key = os.path.basename(path)
        self.project = project_of(path)
        st = self.cfg.get("docs", {}).get(self.doc_key, {})
        pj = self.cfg.get("projects", {}).get(self.project, {})
        self.cast = pj.get("cast") or st.get("cast", {})
        self.overrides = st.get("overrides", {})
        self.aliases = pj.get("aliases") or st.get("aliases", {})
        self.pron = dict(pj.get("pron", {}))
        self.char_color = dict(pj.get("colors", {}))
        self._hist.clear(); self._fut.clear(); self._card_sig.clear(); self._update_undo_buttons()
        self.emo_over = dict(st.get("emo", {}))
        self.heard = set(st.get("heard", []))
        self._resume_pos = int(st.get("pos", 0) or 0)
        self.marks = list(st.get("marks", []))
        self._cur_span = None
        self._add_recent(path)
        self.plan_chunks, self.plan_stat = [], []
        self.segs = []           # el análisis era del documento anterior: nada se narra hasta leer este
        self.foreign = []
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        self.txt.edit_reset()
        self.txt.mark_set("insert", "1.0")
        self.txt.see("1.0")
        self._show_drop(False)
        self.lbl_doc.configure(text=f"📄 {self.project} / {self.doc_key}" if self.project != "_sin_titulo"
                               else f"📄 {self.doc_key}", text_color=TXT)
        self.status("Documento cargado. Kuon IA está leyendo la historia…"
                    + ("  ·  F7 sigue donde lo dejaste." if 0 < self._resume_pos < len(text) else ""))
        self.tabs.set("Reparto")
        self.after(200, self._fix_missing_voices)
        self.schedule_analysis(10)

    def save_text(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Texto", "*.txt")],
                                         initialfile=os.path.splitext(self.doc_key)[0] + ".txt")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.get_text())
            self.status(f"Texto guardado: {p}")

    def get_text(self):
        return self.txt.get("1.0", "end-1c")

    # ------------------------------------------------------------ análisis
    def _on_modified(self, _=None):
        if self.txt.edit_modified():
            self.txt.edit_modified(False)
            if self.get_text().strip():
                self._show_drop(False)
            self.schedule_analysis(1400)

    def schedule_analysis(self, delay):
        if self.analyze_job:
            self.after_cancel(self.analyze_job)
        self.analyze_job = self.after(delay, self._run_analysis)

    def _ana_finish(self):
        self._ana_running = False
        if getattr(self, "_ana_pending", False):
            self._ana_pending = False
            self._run_analysis()

    def _segs_ready(self, retry, what):
        """True si el análisis es del texto que hay ahora en el editor. Si no (documento recién abierto o
        texto cambiado), pide la lectura y repite la acción sola al terminar: así nunca se narra el texto viejo."""
        if self.segs and getattr(self, "_segs_text", None) == self.get_text():
            return True
        self._after_ana = retry
        if not (self.analyze_job or getattr(self, "_ana_running", False)):
            self.schedule_analysis(10)
        self.status(f"Kuon IA está leyendo el texto… {what} empezará en cuanto termine.")
        return False

    def _run_analysis(self):
        self.analyze_job = None
        if getattr(self, "_ana_running", False):       # una lectura a la vez; los cambios de mientras van en la siguiente
            self._ana_pending = True
            return
        text = self.get_text()
        self.analysis_gen += 1
        gen = self.analysis_gen
        names = {v for v in self.overrides.values() if v}
        fixed = {v: self._canon_name(v) for v in names}
        if any(a != b for a, b in fixed.items()):      # «henrietta» guardado de antes -> «Henrietta»
            self.overrides = {k: fixed.get(v, v) for k, v in self.overrides.items()}
        self.aliases = brain.clean_aliases(self.aliases)[0]
        ov, al = dict(self.overrides), dict(self.aliases)
        gov = getattr(self, "_gender_override", {})
        brain.merge_base(self.memory, BASE_MEMORY, self.project)
        mem = json.loads(json.dumps(self.memory))
        proj, th = self.project, float(self.v_threshold.get())
        self.chip_ai.configure(text="◌ Kuon IA analizando…", text_color=AMBER)
        self._ana_running = True

        def work():
            try:
                segs, chars, info = brain.analyze(text, ov, al, memory=mem, project=proj, threshold=th,
                                                  doc=self.doc_key)
                for nm, g in gov.items():
                    if nm in chars:
                        chars[nm].gender = g
                for nm, a in (mem.get("ages") or {}).items():       # edades que elegiste antes
                    if nm in chars:
                        chars[nm].age = a
                chapters = docs.split_chapters(text)
                try:
                    info["foreign"] = docs.detect_foreign(text)
                except Exception as e:
                    self._log_error(e)
                    info["foreign"] = []
                self.after(0, lambda: self._analysis_done(gen, text, segs, chars, chapters, info))
            except Exception as e:
                self._log_error(e)
                self.after(0, lambda e=e: self.status(f"Error en el análisis: {e}"))
            finally:
                self.after(0, self._ana_finish)
        threading.Thread(target=work, daemon=True).start()

    def _analysis_done(self, gen, text, segs, chars, chapters, info=None):
        if gen != self.analysis_gen or getattr(self, "_ana_pending", False):     # ya viene una lectura más nueva
            return
        self.segs, self.chars, self.chapters = segs, chars, chapters
        self._segs_text = text
        cb, self._after_ana = getattr(self, "_after_ana", None), None
        if cb:
            self.after(50, cb)       # ▶ o Generar que se pulsaron mientras Kuon IA leía
        moved = False
        for s in segs:      # correcciones guardadas con la clave vieja (texto#n) pasan a la clave nueva
            if s.okey and s.okey != s.key:
                if s.okey in self.overrides and s.key not in self.overrides:
                    self.overrides[s.key] = self.overrides.pop(s.okey)
                    moved = True
                if s.okey in self.emo_over and s.key not in self.emo_over:
                    self.emo_over[s.key] = self.emo_over.pop(s.okey)
                    moved = True
        if moved:
            self._autosave()
        self.info = info or {}
        if self.info.get("style_model") is not None:
            self.memory.setdefault("style", {}).setdefault(self.project, {})[self.doc_key] = self.info["style_model"]
            save_memory(self.memory)
            self._update_memory_label()
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        self._line_starts = starts
        for nm, c in self.cast.items():
            if nm in chars and c.get("auto") and c.get("gender") != chars[nm].gender:
                c["_regender"] = True
        for nm in [n for n, c in self.cast.items() if c.pop("_regender", False)]:
            self.cast.pop(nm)
        for i, nm in enumerate(chars):
            self.char_color.setdefault(nm, CHAR_COLORS[len(self.char_color) % len(CHAR_COLORS)])
        if self.v_autocast.get():
            self._autocast()
        self._render_cast()
        self._render_chapters()
        self._set_foreign(self.info.get("foreign", []))
        self.recolor()
        nd = sum(1 for s in segs if ia_dudosos.is_doubtful(s))
        self.btn_review.configure(text=f"🔍 Revisar dudosos ({nd})" if nd else "🔍 Revisar dudosos",
                                  text_color=AMBER if nd else TXT)
        self._rv_after_analysis()
        self._update_stats_light()
        self.chip_ai.configure(text="◉ Kuon IA" + (" + Ollama" if self.ollama_models else ""), text_color=GREEN)
        self.schedule_plan(150)
        d, k = brain.stats(segs)
        if d:
            doubt = sum(1 for s in segs if s.kind == "dlg" and s.guess)
            extra = []
            if doubt:
                extra.append(f"{doubt} dudosos → narrador")
            if self.info.get("auto_aliases"):
                extra.append("unió " + ", ".join(f"{a}={b}" for a, b in list(self.info["auto_aliases"].items())[:3]))
            if self.info.get("script"):
                extra.append("formato guion")
            if self.info.get("first_person"):
                extra.append("narrador en 1ª persona")
            if self.info.get("style_changes"):
                extra.append(f"estilo: {self.info['style_changes']} líneas ajustadas")
            self.status(f"Kuon IA: {len(chars)} personajes, {k}/{d} con voz" + (" · " + " · ".join(extra) if extra else ""))
            if (not self.cfg.get("tip_math") and self._math_mode() != "off"
                    and sum(1 for sg in segs[:600] if estudio.is_math(sg.text)) >= 2):
                self.cfg["tip_math"] = True
                self.after(2500, lambda: self.status("∑ Encontré fórmulas: se leerán en español. Clic derecho › «Cómo se "
                                                     "leerá esto» para oírlas antes; el botón ∑ lo cambia."))

    def _autocast(self):
        narr = self._vname(self.v_narr.get())
        self.cast = vx.auto_cast(list(self.chars.values()), self.voices, self.v_lang.get(), narr, self.cast,
                                 neutral=self.v_variant.get())

    def recast_all(self):
        self._checkpoint("repartir voces")
        self.cast = {n: c for n, c in self.cast.items() if not c.get("auto")}
        self._autocast()
        self._render_cast()
        self.status("Voces repartidas de nuevo (se mantienen las que elegiste a mano).")

    def _idx(self, off):
        ln = bisect.bisect_right(self._line_starts, off) - 1
        return f"{ln + 1}.{off - self._line_starts[ln]}"

    def _off(self, index):
        ln, col = map(int, self.txt.index(index).split("."))
        if ln - 1 < len(self._line_starts):
            return self._line_starts[ln - 1] + col
        return 0

    def recolor(self):
        for tag in self.txt.tag_names():
            if tag.startswith("sp_") or tag in ("title", "tagline", "unknown", "doubt"):
                self.txt.tag_remove(tag, "1.0", "end")
        if not self.v_colors.get() or not self.segs:
            for s in self.segs:
                if s.kind == "title":
                    self.txt.tag_add("title", self._idx(s.start), self._idx(s.end))
            return
        for nm, col in self.char_color.items():
            self.txt.tag_configure(f"sp_{nm}", foreground=self._read_col(col))
        segs = list(self.segs)
        gen = self.analysis_gen

        def batch(i=0):
            if gen != self.analysis_gen:
                return
            for s in segs[i:i + 1500]:
                a, b = self._idx(s.start), self._idx(s.end)
                if s.kind == "title":
                    self.txt.tag_add("title", a, b)
                elif s.kind == "dlg":
                    self.txt.tag_add(f"sp_{s.speaker}" if s.speaker else "unknown", a, b)
                    if ia_dudosos.is_doubtful(s):          # subrayado ámbar: conviene revisarlo
                        self.txt.tag_add("doubt", a, b)
                elif s.kind == "narr" and s.pause == "inner" or (s.kind == "narr" and len(s.text) < 70
                                                                  and brain.RE_SPEECH.match(s.text)):
                    self.txt.tag_add("tagline", a, b)
            if i + 1500 < len(segs):
                self.after(1, lambda: batch(i + 1500))
            self.txt.tag_raise("reading")
        batch()

    def _update_stats_light(self):
        text = self.get_text()
        words = len(text.split())
        mins = words / (155 * max(self.v_speed.get(), 0.5))
        d, k = brain.stats(self.segs)
        h, m = divmod(int(mins), 60)
        dur = f"{h} h {m:02d} min" if h else f"{m} min"
        self.lbl_stats.configure(text=f"{words:,} palabras  ·  ~{dur}  ·  {len(self.chars)} personajes"
                                 .replace(",", "."))

    # ------------------------------------------------------------ deshacer / rehacer
    def _save_mem(self):
        save_memory(self.memory)

    _MEM_KEYS = ("aliases", "genders", "ages", "not_names", "epithets", "verbs", "not_alias")

    def _snapshot(self):
        st = {"overrides": self.overrides, "aliases": self.aliases, "cast": self.cast, "emo": self.emo_over,
              "colors": self.char_color, "pron": self.pron, "gov": getattr(self, "_gender_override", {}),
              "lex": self.lex.get("1.0", "end-1c") if hasattr(self, "lex") else None,
              "mem": {k: self.memory.get(k) for k in self._MEM_KEYS}}
        return json.loads(json.dumps(st, ensure_ascii=False, default=str))

    def _checkpoint(self, label, merge_s=0.0):
        """Guarda el estado antes de un cambio del usuario. merge_s: cambios seguidos del mismo tipo
        (mover el tono) se deshacen de una vez."""
        if getattr(self, "_in_batch", False):
            return
        now = time.time()
        if merge_s and self._last_ck[0] == label and now - self._last_ck[1] < merge_s and self._hist:
            self._last_ck = (label, now)
            return
        self._last_ck = (label, now)
        self._hist.append((label, self._snapshot()))
        del self._hist[:-80]
        self._fut.clear()
        self._update_undo_buttons()
        self._autosave()

    def _restore(self, st):
        self.overrides, self.aliases, self.cast = st["overrides"], st["aliases"], st["cast"]
        self.emo_over, self.char_color, self.pron = st["emo"], st["colors"], st["pron"]
        self._gender_override = st.get("gov", {})
        if st.get("lex") is not None and hasattr(self, "lex") and self.lex.get("1.0", "end-1c") != st["lex"]:
            self.lex.delete("1.0", "end")
            self.lex.insert("1.0", st["lex"])
        for k, v in st["mem"].items():
            if v is None:
                self.memory.pop(k, None)
            else:
                self.memory[k] = v
        save_memory(self.memory)
        self._update_memory_label()
        self._card_sig.clear()
        self._render_cast()
        self._set_foreign(self.foreign)
        self.schedule_analysis(10)
        self._autosave()

    def undo(self):
        if not self._hist:
            return
        label, st = self._hist.pop()
        self._fut.append((label, self._snapshot()))
        self._restore(st)
        self._update_undo_buttons()
        self.status(f"Deshecho: {label}")

    def redo(self):
        if not self._fut:
            return
        label, st = self._fut.pop()
        self._hist.append((label, self._snapshot()))
        self._restore(st)
        self._update_undo_buttons()
        self.status(f"Rehecho: {label}")

    # ------------------------------------------------------------ teclado
    KEYS = [
        ("Espacio", "Reproducir / pausar (fuera del editor). Ctrl+Espacio funciona en cualquier lugar."),
        ("Ctrl+Enter o F5", "Escuchar desde el cursor (o lo seleccionado)."),
        ("F6", "Escuchar todo desde el inicio."),
        ("Esc", "Detener."),
        ("Alt+→ / Alt+←", "Párrafo siguiente / anterior. Fuera del editor bastan → y ←."),
        ("Ctrl+Z / Ctrl+Y", "Deshacer / rehacer cambios del reparto (en el editor deshacen el texto)."),
        ("Ctrl+O / Ctrl+S", "Abrir documento / guardar el texto."),
        ("Ctrl+G", "Generar el audio final."),
        ("Ctrl+N", "Borrar todo y empezar un documento nuevo (Ctrl+Z lo recupera)."),
        ("Ctrl+A", "Seleccionar todo el texto."),
        ("Ctrl+F / Ctrl+H / F3", "Buscar / buscar y reemplazar / siguiente coincidencia."),
        ("F7", "Continuar donde lo dejaste la última vez."),
        ("F8", "Leer en voz alta lo que copiaste en cualquier programa."),
        ("Ctrl+rueda / Ctrl+0", "Letra del editor más grande o más pequeña / tamaño normal."),
        ("Win+H", "Dictar con la voz (dictado de Windows), también en «☰ Más»."),
        ("Ctrl+1 … Ctrl+7", "Pestañas: Reparto, Voz, IA, Música, Exportar, Dicción, Asistente."),
        ("Ctrl++ / Ctrl+-", "Más o menos velocidad."),
        ("Ctrl+K", "Escribir al asistente."),
        ("F1", "Ayuda."),
    ]

    def _bind_keys(self):
        def k(seq, fn, typing_ok=True):
            def h(e):
                if not typing_ok and self._typing_focus():
                    return None
                try:
                    fn()
                except Exception as ex:
                    self._log_error(ex)
                return "break"
            self.bind_all(seq, h, add="+")
        k("<space>", self.play_toggle, typing_ok=False)
        k("<Control-space>", self.play_toggle)
        k("<Control-Return>", self.preview)
        k("<F5>", self.preview)
        k("<F6>", self.preview_all)
        def esc():
            try:        # Esc en un menú o una ventanita cierra eso, no detiene la lectura
                f = self.focus_get()
                if f is not None and f.winfo_toplevel() is not self:
                    return
            except (KeyError, tk.TclError):
                return
            self.stop_all()
        k("<Escape>", esc)
        k("<Alt-Right>", lambda: self.jump_para(1))
        k("<Alt-Left>", lambda: self.jump_para(-1))
        k("<Right>", lambda: self.jump_para(1), typing_ok=False)
        k("<Left>", lambda: self.jump_para(-1), typing_ok=False)
        for seq in ("<Control-o>", "<Control-O>"):
            k(seq, self.open_dialog)
        for seq in ("<Control-s>", "<Control-S>"):
            k(seq, self.save_text)
        for seq in ("<Control-g>", "<Control-G>"):
            k(seq, self.generate)
        tabs = ("Reparto", "Voz", "IA", "Música", "Exportar", "Dicción", "Asistente")
        for i, name in enumerate(tabs, 1):
            k(f"<Control-Key-{i}>", lambda n=name: self.tabs.set(n))
        k("<Control-plus>", lambda: self._speed_step(0.05))
        k("<Control-equal>", lambda: self._speed_step(0.05))
        k("<Control-KP_Add>", lambda: self._speed_step(0.05))
        k("<Control-minus>", lambda: self._speed_step(-0.05))
        k("<Control-KP_Subtract>", lambda: self._speed_step(-0.05))
        for seq in ("<Control-k>", "<Control-K>"):
            k(seq, lambda: (self.tabs.set("Asistente"), self.ai_in.focus_set()))
        k("<F1>", self.show_help)
        for seq in ("<Control-n>", "<Control-N>"):
            k(seq, self.clear_all)
        for seq in ("<Control-f>", "<Control-F>"):
            k(seq, lambda: self.find_replace(False))
        for seq in ("<Control-h>", "<Control-H>"):
            k(seq, lambda: self.find_replace(True))
        k("<F3>", self.find_next)
        k("<F7>", self.resume_reading)
        k("<F8>", self.read_clipboard)
        for seq in ("<Control-b>", "<Control-B>"):
            k(seq, self.add_mark)
        k("<F9>", self.marks_window)
        k("<Control-0>", lambda: self.zoom_text(0))

    def _speed_step(self, d):
        v = round(max(0.5, min(3.0, float(self.v_speed.get()) + d)), 2)
        self.v_speed.set(v)
        self.status(f"Velocidad {v:.2f}x (se aplica a lo que se genere desde ahora)")

    def _cur_plan_index(self):
        p = self.play
        if p and p.get("from_plan") and 0 <= p.get("idx", -1) < len(p.get("parts", [])):
            chunks = p["parts"][p["idx"]][1]
            if 0 <= p.get("cur", -1) < len(chunks):
                gi = getattr(chunks[p["cur"]], "_i", None)
                if gi is not None:
                    return gi
        return self._plan_index_at(self._off("insert"))

    def _para_start(self, j):
        if j <= 0:
            return True
        a, b = self.plan_chunks[j - 1], self.plan_chunks[j]
        return a.chapter != b.chapter or "\n" in self.get_text()[a.src_end:b.src_start]

    def jump_para(self, d):
        """⏮ ⏭: salta al párrafo anterior o siguiente y sigue escuchando desde ahí."""
        if not self.segs:
            return
        if not self._plan_fresh() or not self.plan_chunks:
            self.schedule_plan(10)
            self.status("Preparando la línea de tiempo… vuelve a pulsar en un momento.")
            return
        n = len(self.plan_chunks)
        i = self._cur_plan_index()
        if d > 0:
            i += 1
            while i < n and not self._para_start(i):
                i += 1
            if i >= n:
                self.status("Es el último párrafo.")
                return
        else:
            p0 = i
            while p0 > 0 and not self._para_start(p0):
                p0 -= 1
            p = self.play
            early = True
            if p and 0 <= p.get("cur", -1) < len(p.get("starts", [])):
                early = (time.time() - p["part_t0"] - p["starts"][p["cur"]]) < 1.5
            if not p or (p0 == i and early):
                p0 -= 1
                while p0 > 0 and not self._para_start(p0):
                    p0 -= 1
            i = max(0, p0)
        try:
            self.txt.mark_set("insert", self._idx(self.plan_chunks[i].src_start))
        except Exception:
            pass
        self.play_plan(i)

    def _typing_focus(self):
        w = self.focus_get()
        cls = w.winfo_class() if w else ""
        return cls in ("Text", "Entry", "TEntry", "Spinbox")

    def _key_undo(self, _e=None):
        if self._typing_focus():
            return None           # en el editor, Ctrl+Z deshace el texto
        self.undo()
        return "break"

    def _key_redo(self, _e=None):
        if self._typing_focus():
            return None
        self.redo()
        return "break"

    def _update_undo_buttons(self):
        if not hasattr(self, "btn_undo"):
            return
        self.btn_undo.configure(state="normal" if self._hist else "disabled",
                                text_color=TXT if self._hist else MUTED)
        self.btn_redo.configure(state="normal" if self._fut else "disabled",
                                text_color=TXT if self._fut else MUTED)

    def _autosave(self):
        """Guarda reparto y correcciones a los 2 s de cada cambio: si la PC se apaga no se pierde nada."""
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(2000, self._autosave_now)

    def _autosave_now(self):
        self._save_job = None
        try:
            self._store()
        except Exception as e:
            self._log_error(e)

    def _on_resize(self, e):
        if e.widget is self:
            self._resize_until = time.time() + 0.25

    # ------------------------------------------------------------ colores
    PALETTE = ["#22d3ee", "#38bdf8", "#3b82f6", "#6366f1", "#a78bfa", "#c084fc", "#e879f9", "#f472b6",
               "#fb7185", "#f87171", "#fb923c", "#f5a524", "#facc15", "#a3e635", "#34d399", "#2dd4bf",
               "#94a3b8", "#e2e8f0"]

    def _pick_color(self, nm, anchor):
        m = tk.Menu(self, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, font=("Segoe UI", 10))
        for col in self.PALETTE:
            m.add_command(label="  ██████  ", foreground=col, activeforeground=col,
                          command=lambda col=col: self._set_color(nm, col))
        m.add_separator()
        m.add_command(label="Otro color…", command=lambda: self._set_color(nm, None))
        try:
            m.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            m.grab_release()

    def _set_color(self, nm, col):
        if col is None:
            from tkinter import colorchooser
            col = colorchooser.askcolor(color=self.char_color.get(nm, CYAN), title=f"Color de {nm}",
                                        parent=self)[1]
            if not col:
                return
        self._checkpoint(f"color de {nm}")
        self.char_color[nm] = col
        self._card_sig.pop(nm, None)
        self._render_cast()
        self.recolor()
        self._draw_timeline()

    # ------------------------------------------------------------ reparto UI
    def _render_cast(self):
        names = [n for n in self.chars if n in self.cast] + [n for n in self.cast if n not in self.chars]
        for w in self.cast_list.winfo_children():
            w.pack_forget()
        d, k = brain.stats(self.segs)
        unknown = d - k
        if names:
            self.lbl_cast_info.configure(
                text=f"{len(names)} personajes · {k} de {d} diálogos asignados"
                     + (f" · {sum(1 for n in names if self._gender_doubt(n))} con género dudoso (en ámbar)"
                        if any(self._gender_doubt(n) for n in names) else "")
                     + (f" · {unknown} sin hablante seguro (los lee el narrador). Clic derecho en un diálogo para corregir."
                        if unknown else ". Clic derecho en un diálogo para corregir."))
        if not self.v_charvoices.get():
            self.lbl_cast_info.configure(text="Modo «Solo el narrador»: toda la historia se lee con la voz del narrador. "
                                              "Cámbialo en la pestaña Voz.")
        labels = [v.label for v in self._lang_voices()]
        limit = min(len(names), 150) if getattr(self, "_show_all_cast", False) else 30     # tope: recursos de Windows
        shown = names[:limit]
        # Solo se reconstruyen las tarjetas que cambiaron: antes se rehacían todas en cada análisis.
        # Cada tarjeta tarda ~0,1 s en crearse: se hacen de 3 en 3 entre eventos para que la ventana no se congele
        # al abrir una obra con muchos personajes.
        todo = []
        for nm in shown:
            c, ch = self.cast[nm], self.chars.get(nm)
            sig = json.dumps([c, ch.lines if ch else 0, self._gender_doubt(nm), self.char_color.get(nm), self._voice_twins(nm),
                              tuple(o for o in names if o != nm)[:60], len(labels)], default=str, sort_keys=True)
            card = self._cards.get(nm)
            if card is None or self._card_sig.get(nm) != sig or not card.winfo_exists():
                todo.append((nm, sig))
        for nm, card in list(self._cards.items()):
            if nm not in shown:
                try:
                    card.destroy()
                except Exception:
                    pass
                self._cards.pop(nm, None)
                self._card_sig.pop(nm, None)
        gen = self._cast_gen = getattr(self, "_cast_gen", 0) + 1

        def repack():
            for w in self.cast_list.winfo_children():
                w.pack_forget()
            for nm in shown:
                card = self._cards.get(nm)
                if card is not None and card.winfo_exists():
                    card.pack(fill="x", pady=4, padx=2)

        def step(i):
            if gen != self._cast_gen:          # llegó un reparto más nuevo: este se abandona
                return
            for nm, sig in todo[i:i + 3]:
                old = self._cards.get(nm)
                if old is not None:
                    try:
                        old.destroy()
                    except Exception:
                        pass
                self._card(nm, labels, names, pack=False)
                self._card_sig[nm] = sig
            if i + 3 < len(todo):
                if i % 9 == 0:
                    repack()
                self.after(1, lambda: step(i + 3))
                return
            repack()
            keep = set(self._cards.values())
            for w in self.cast_list.winfo_children():
                if w not in keep and not w.winfo_manager():
                    w.destroy()
            if len(names) > limit:
                self._btn(self.cast_list, f"Mostrar {len(names) - limit} personajes más",
                          lambda: (setattr(self, "_show_all_cast", True), self._render_cast()),
                          width=260).pack(pady=8)
        step(0)

    def _card(self, nm, labels, all_names=(), pack=True):
        c = self.cast[nm]
        ch = self.chars.get(nm)
        col = self.char_color.setdefault(nm, CHAR_COLORS[len(self.char_color) % len(CHAR_COLORS)])
        card = ctk.CTkFrame(self.cast_list, fg_color=SURF2, corner_radius=12, border_width=1, border_color=LINE)
        if pack:
            card.pack(fill="x", pady=4, padx=2)
        strip = ctk.CTkFrame(card, fg_color=col, width=4, height=10, corner_radius=2)
        strip.pack(side="left", fill="y", padx=(6, 0), pady=8)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        sw = ctk.CTkButton(top, text="", width=18, height=18, corner_radius=9, fg_color=col, hover_color=col,
                           border_width=1, border_color=LINE)
        sw.configure(command=lambda b=sw: self._pick_color(nm, b))
        sw.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(top, text=nm, font=font(14, "bold"), text_color=col).pack(side="left")
        g = c.get("gender", "M")
        dq = self._gender_doubt(nm)
        ctk.CTkButton(top, text=("¿" if dq else "") + ("♀" if g == "F" else "♂") + ("?" if dq else ""),
                      width=26, height=22, corner_radius=11, fg_color=SURF3, hover_color=CYAN_D, font=font(12),
                      border_width=1 if dq else 0, border_color=AMBER, text_color=AMBER if dq else TXT,
                      command=lambda: self._toggle_gender(nm)).pack(side="left", padx=6)
        age = ctk.CTkOptionMenu(top, values=AGES, width=86, height=22, fg_color=SURF3, button_color=SURF3,
                                button_hover_color=CYAN_D, dropdown_fg_color=SURF2, font=font(11),
                                command=lambda v: self._set_age(nm, v))
        age.set(c.get("age", "adulto"))
        age.pack(side="left")
        lines = ch.lines if ch else 0
        ctk.CTkLabel(top, text=f"{lines} líneas", font=font(11), text_color=MUTED).pack(side="left", padx=8)
        ctk.CTkButton(top, text="✕", width=24, height=22, fg_color="transparent", hover_color=RED,
                      text_color=MUTED, command=lambda: self._remove_char(nm)).pack(side="right")
        others = [o for o in all_names if o != nm][:60]
        if others:
            mg = ctk.CTkOptionMenu(top, values=others, width=30, height=22, fg_color=SURF3,
                                   button_color=SURF3, button_hover_color=CYAN_D, dropdown_fg_color=SURF2,
                                   font=font(11), command=lambda target: self._merge(nm, target))
            mg.set("⇄")
            mg.pack(side="right", padx=2)
        ctk.CTkButton(top, text="▶", width=28, height=22, fg_color=SURF3, hover_color=CYAN_D,
                      command=lambda: self.test_char(nm)).pack(side="right", padx=4)
        if dq:
            q = ctk.CTkFrame(body, fg_color="#2a2012", corner_radius=8, border_width=1, border_color="#6b4a12")
            q.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(q, text=f"¿{nm} es mujer u hombre? {self._gender_why(ch)}", font=font(11),
                         text_color="#fcd9a0", wraplength=330, justify="left").pack(anchor="w", padx=8, pady=(6, 2))
            qr = ctk.CTkFrame(q, fg_color="transparent")
            qr.pack(anchor="w", padx=6, pady=(0, 6))
            for lab_, gg in (("♀ Mujer", "F"), ("♂ Hombre", "M")):
                ctk.CTkButton(qr, text=lab_, width=80, height=24, fg_color=SURF3, hover_color=CYAN_D,
                              font=font(11), command=lambda gg=gg: self._toggle_gender(nm, gg)).pack(side="left", padx=2)
        var = tk.StringVar(value=self._vlabel(c["voice"]))
        cb = ctk.CTkComboBox(body, variable=var, values=labels, state="readonly", height=28,
                             fg_color=SURF3, border_color=LINE, button_color=SURF3, button_hover_color=CYAN_D,
                             dropdown_fg_color=SURF2, dropdown_hover_color=CYAN_D, font=font(12),
                             command=lambda v: self._set_voice(nm, v))
        cb.pack(fill="x", pady=(6, 2))
        prow = ctk.CTkFrame(body, fg_color="transparent")
        prow.pack(fill="x")
        ctk.CTkLabel(prow, text="Tono", font=font(11), text_color=MUTED, width=34).pack(side="left")
        pv = tk.DoubleVar(value=c.get("pitch", 0))
        lab = ctk.CTkLabel(prow, text=f"{int(pv.get()):+d} Hz", font=font(11, "bold"), text_color=col, width=52)

        def on_pitch(v):
            self._checkpoint(f"tono de {nm}", merge_s=1.5)
            self.cast[nm]["pitch"] = int(round(v))
            self.cast[nm]["auto"] = False
            lab.configure(text=f"{int(round(v)):+d} Hz")
        ctk.CTkSlider(prow, from_=-30, to=30, number_of_steps=60, variable=pv, command=on_pitch, height=14,
                      progress_color=col, button_color=TXT, button_hover_color=col, fg_color=SURF3).pack(
            side="left", fill="x", expand=True, padx=4)
        lab.pack(side="left")
        tw = self._voice_twins(nm)
        if tw:
            ctk.CTkLabel(body, text=f"⚠ Suena casi igual que {', '.join(tw[:3])}: cámbiale la voz o mueve el tono.",
                         font=font(11), text_color=AMBER, wraplength=330, justify="left").pack(anchor="w", pady=(2, 0))
        self._cards[nm] = card

    def _voice_twins(self, nm):
        """Personajes con la misma voz y casi el mismo tono (idea de ElevenLabs Studio): el oyente no los distingue."""
        c = self.cast.get(nm) or {}
        if not c.get("voice") or not self.v_charvoices.get():
            return []
        return [o for o, oc in self.cast.items()
                if o != nm and o in self.chars and oc.get("voice") == c.get("voice") and not oc.get("muted")
                and abs(int(oc.get("pitch", 0)) - int(c.get("pitch", 0))) < 4]

    def _gender_doubt(self, nm):
        ch, c = self.chars.get(nm), self.cast.get(nm, {})
        ev = getattr(ch, "evidence", {}) if ch else {}
        return bool(ch and ev.get("from") == "texto" and ch.confidence < 0.35
                    and nm not in self.memory.get("genders", {}) and c.get("auto", True))

    @staticmethod
    def _gender_why(ch):
        ev = getattr(ch, "evidence", {}) or {}
        a = (f"En el texto hay {ev.get('fem', 0):g} puntos de mujer y {ev.get('mas', 0):g} de hombre"
             if ev.get("fem") or ev.get("mas") else "El texto casi no da pistas")
        b = (f"el nombre suele ser de {'mujer' if ev['name'] == 'F' else 'hombre'}" if ev.get("name")
             else "el nombre no lo aclara")
        return f"{a}; {b}."

    def _toggle_gender(self, nm, g=None):
        self._checkpoint(f"género de {nm}")
        c = self.cast[nm]
        g = g or ("F" if c.get("gender") == "M" else "M")
        if nm in self.chars:
            self.chars[nm].gender = g
            self.chars[nm].evidence = {"from": "tú"}
        pool = vx.ordered_pool(self.voices, self.v_lang.get(), "Female" if g == "F" else "Male", self.v_variant.get())
        used = {x["voice"] for x in self.cast.values()} | {self._vname(self.v_narr.get())}
        free = [v for v in pool if v.name not in used] or pool
        if free:
            c["voice"] = free[0].name
        c["gender"], c["pitch"], c["auto"] = g, (0 if g == "F" else -3), False
        self.memory.setdefault("genders", {})[nm] = g
        save_memory(self.memory)
        self._update_memory_label()
        self._render_cast()
        self.status(f"{nm}: {'mujer' if g == 'F' else 'hombre'}. Kuon IA lo recordará en toda la obra.")

    def _set_age(self, nm, age):
        self._checkpoint(f"edad de {nm}")
        self.memory.setdefault("ages", {})[nm] = age        # Kuon IA recuerda la edad en toda la obra
        save_memory(self.memory)
        c = self.cast[nm]
        old = vx.AGE_MOD.get(c.get("age", "adulto"), (0, 0))
        new = vx.AGE_MOD.get(age, (0, 0))
        c["pitch"] = int(c.get("pitch", 0) - old[0] + new[0])
        c["rate"] = int(c.get("rate", 0) - old[1] + new[1])
        c["age"], c["auto"] = age, False
        self._render_cast()

    def _set_voice(self, nm, label):
        self._checkpoint(f"voz de {nm}")
        self.cast[nm]["voice"] = self._vname(label)
        self.cast[nm]["auto"] = False
        g = next((v.gender for v in self.voices if v.name == self.cast[nm]["voice"]), None)
        if g:
            self.cast[nm]["gender"] = "F" if g == "Female" else "M"
        self.after(50, self._render_cast)       # actualiza los avisos de «suena igual que…»

    def _merge(self, nm, target, ask=True):
        if ask and not messagebox.askyesno(APP, f"¿«{nm}» y «{target}» son el mismo personaje?\n\n"
                                                f"Los diálogos de {nm} pasarán a la voz de {target}."):
            self._render_cast()
            return
        mem_al = self.memory.setdefault("aliases", {})
        seen = {nm}
        while target in self.aliases and target not in seen:      # A -> B -> C: se fusiona directo con C
            seen.add(target)
            target = self.aliases[target]
        if target == nm or target.lower() == nm.lower():
            self._render_cast()
            return
        self._checkpoint(f"unir {nm} con {target}")
        for d in (self.aliases, mem_al):          # nada puede apuntar en círculo hacia nm
            d.pop(target, None)
            for k in [k for k, v in d.items() if v == nm]:
                d[k] = target
        self.aliases[nm] = target
        mem_al[nm] = target
        self.memory["not_names"] = [n for n in self.memory.get("not_names", []) if n.lower() != target.lower()]
        save_memory(self.memory)
        self._update_memory_label()
        self.cast.pop(nm, None)
        self.schedule_analysis(10)

    def _remove_char(self, nm, ask=True):
        if ask and not messagebox.askyesno(APP, f"¿«{nm}» no es un personaje?\n\nKuon IA lo recordará y no lo volverá "
                                        "a tratar como personaje. Sus líneas pasan al narrador."):
            return
        self._checkpoint(f"quitar {nm}")
        if nm not in self.memory.setdefault("not_names", []):
            self.memory["not_names"].append(nm)
        low = nm.lower()
        for d in (self.aliases, self.memory.setdefault("aliases", {})):     # sus fusiones dejan de valer
            for k in [k for k, v in d.items() if k.lower() == low or v.lower() == low]:
                d.pop(k)
        for k in [k for k, v in self.overrides.items() if v and v.lower() == low]:
            self.overrides.pop(k)
        for pj in self.memory.get("epithets", {}).values():
            for e in [e for e, v in pj.items() if v.lower() == low]:
                pj.pop(e)
        save_memory(self.memory)
        self._update_memory_label()
        self.schedule_analysis(10)
        self.cast.pop(nm, None)
        if nm in self.chars:
            self.chars.pop(nm)
        self._render_cast()

    def _canon_name(self, nm):
        """El nombre que escribe el usuario, con la forma que ya usa la app o el texto
        («henrietta» -> «Henrietta»), para no crear dos personajes iguales."""
        nm = re.sub(r"\s+", " ", (nm or "").strip())
        if not nm:
            return ""
        low = nm.lower()
        for n in list(self.chars) + list(self.cast):
            if n.lower() == low:
                return n
        nm = self.aliases.get(nm, nm)
        for m in re.finditer(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", self.get_text(), re.IGNORECASE):
            if m.group(0)[:1].isupper():        # la primera vez que sale con mayúscula, no la primera a secas
                return m.group(0)
        return nm[:1].upper() + nm[1:] if nm.islower() and " " not in nm else nm

    def _allow_name(self, nm):
        """Si el usuario usa un nombre que antes marcó como «no es personaje», deja de estar descartado."""
        nl = self.memory.get("not_names", [])
        if any(n.lower() == nm.lower() for n in nl):
            self.memory["not_names"] = [n for n in nl if n.lower() != nm.lower()]

    def add_character(self):
        d = ctk.CTkInputDialog(text="Nombre del personaje tal como aparece en el texto:", title="Añadir personaje",
                               fg_color=SURF, button_fg_color=CYAN_D, entry_fg_color=SURF2)
        nm = self._canon_name(d.get_input())
        if not nm:
            return
        self._allow_name(nm)
        save_memory(self.memory)
        if nm in self.cast:
            self.status(f"«{nm}» ya está en el reparto.")
            return
        self._checkpoint(f"añadir {nm}")
        g, _ = brain.guess_gender_by_name(nm)
        ch = brain.Character(nm, 0, g)
        narr = self._vname(self.v_narr.get())
        self.cast = vx.auto_cast([ch], self.voices, self.v_lang.get(), narr, self.cast, neutral=self.v_variant.get())
        self.cast[nm]["auto"] = False
        self.char_color.setdefault(nm, CHAR_COLORS[len(self.char_color) % len(CHAR_COLORS)])
        self._render_cast()

    # --- clic derecho en el editor
    def _context_menu(self, e):
        idx = self.txt.index(f"@{e.x},{e.y}")
        off = self._off(idx)
        seg = next((s for s in self.segs if s.start <= off <= s.end and s.kind == "dlg"), None)
        m = tk.Menu(self, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, activeforeground=TXT,
                    font=("Segoe UI", 10), borderwidth=0)
        m.add_command(label="▶  Escuchar desde aquí", command=lambda: self.preview_from(off))
        if seg:
            m.add_separator()
            who = seg.speaker or (f"narrador (dudaba: ¿{seg.guess}?)" if seg.guess else "narrador")
            info = f"Lo dice: {who}  ·  {brain.EMO_LABEL.get(seg.emotion, seg.emotion)}"
            if seg.source:
                info += f"  ·  ({seg.source})"
            m.add_command(label=info, state="disabled")
            sub = tk.Menu(m, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, font=("Segoe UI", 10))
            for nm in list(self.cast)[:40]:
                sub.add_command(label=nm, command=lambda n=nm: self._override(seg, n))
            sub.add_separator()
            sub.add_command(label="Narrador / grupo", command=lambda: self._override(seg, ""))
            sub.add_command(label="Nuevo personaje…", command=lambda: self._override_new(seg))
            m.add_cascade(label="Asignar este diálogo a", menu=sub)
            em = tk.Menu(m, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, font=("Segoe UI", 10))
            cur = self.emo_over.get(seg.key, "")
            for k_, lab_ in EMO_CHOICES:
                em.add_command(label=("● " if k_ == cur else "   ") + lab_,
                               command=lambda k_=k_: self._set_line_emotion(seg, k_))
            m.add_cascade(label="Cómo lo dice (emoción)", menu=em)
            if seg.key in self.overrides:
                m.add_command(label="Quitar corrección manual",
                              command=lambda: (self.overrides.pop(seg.key, None), self.schedule_analysis(10)))
        m.add_separator()
        m.add_command(label="Cortar", accelerator="Ctrl+X", command=lambda: self.txt.event_generate("<<Cut>>"))
        m.add_command(label="Copiar", accelerator="Ctrl+C", command=lambda: self.txt.event_generate("<<Copy>>"))
        m.add_command(label="Pegar", accelerator="Ctrl+V", command=lambda: self.txt.event_generate("<<Paste>>"))
        m.add_command(label="Seleccionar todo", accelerator="Ctrl+A", command=self.select_all)
        m.add_separator()
        m.add_command(label="Buscar y reemplazar…", accelerator="Ctrl+H", command=lambda: self.find_replace(True))
        m.add_command(label="Borrar todo", accelerator="Ctrl+N", command=self.clear_all)
        m.add_separator()
        m.add_command(label="🔊 Cómo se leerá esto (fórmulas)", command=self.show_spoken)
        m.tk_popup(e.x_root, e.y_root)

    def show_spoken(self):
        """Muestra la frase exacta que dirá la voz para lo seleccionado (o el párrafo del cursor)."""
        try:
            src = self.txt.get("sel.first", "sel.last")
        except tk.TclError:
            src = self.txt.get("insert linestart", "insert lineend")
        src = src.strip()
        if not src:
            return
        said = audio.clean_for_speech(estudio.math_speech(src[:1500], self._math_mode()))
        messagebox.showinfo(APP, f"Escrito:\n{src[:600]}\n\nSe leerá (fórmulas: {self.v_math.get().lower()}):\n{said[:900]}"
                                 "\n\nSi un símbolo se lee mal, corrígelo en Dicción con «símbolo = cómo suena».")

    # ------------------------------------------------------------ herramientas del editor (estilo Balabolka)
    def select_all(self):
        self.txt.tag_add("sel", "1.0", "end-1c")
        self.txt.mark_set("insert", "1.0")
        self.txt.focus_set()

    def paste_doc(self, replace):
        """Reemplaza el texto (o lo añade al final) con lo copiado, en un solo paso; Ctrl+Z lo deshace."""
        try:
            t = self.clipboard_get()
        except tk.TclError:
            t = ""
        if not t.strip():
            messagebox.showinfo(APP, "No hay texto copiado. Copia algo (Ctrl+C) y vuelve a intentarlo.")
            return
        self.stop_all()
        self.txt.configure(autoseparators=False)          # borrar + pegar = un solo Ctrl+Z
        self.txt.edit_separator()
        if replace:
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", t)
        else:
            cur = self.txt.get("1.0", "end-1c").rstrip()
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", (cur + "\n\n" if cur else "") + t)
        self.txt.edit_separator()
        self.txt.configure(autoseparators=True)
        self.txt.mark_set("insert", "1.0")
        self.txt.see("1.0")
        self._show_drop(False)
        self.schedule_analysis(10)
        self.status(("Texto reemplazado" if replace else "Texto añadido al final") + " con lo copiado. Ctrl+Z lo deshace.")

    def clear_all(self, ask=True):
        """Borra todo el texto y empieza un documento nuevo. Ctrl+Z lo recupera."""
        if self.busy:
            self.status("Hay un trabajo en curso. Espera a que termine o pulsa Cancelar.")
            return False
        if ask and self.get_text().strip() and not messagebox.askyesno(
                APP, "¿Borrar todo el texto y empezar un documento nuevo?\n\n"
                     "Tu reparto y correcciones del documento actual quedan guardados. "
                     "Si te equivocas, Ctrl+Z recupera el texto."):
            return False
        self._store()
        self.stop_all()
        self.doc_path, self.doc_key, self.project = None, "_sin_titulo", "_sin_titulo"
        self.segs, self.chars, self.chapters = [], {}, [("Texto completo", 0, 0)]
        self._segs_text = None
        self.cast, self.overrides, self.aliases, self.pron = {}, {}, {}, {}
        self.emo_over, self.heard, self.foreign = {}, set(), []
        self.plan_chunks, self.plan_stat = [], []
        self._resume_pos = 0
        self.marks, self._cur_span = [], None
        self.txt.edit_separator()
        self.txt.delete("1.0", "end")
        self.txt.edit_separator()
        self.txt.focus_set()          # así Ctrl+Z va al texto y lo recupera
        self.lbl_doc.configure(text="Sin documento", text_color=MUTED)
        self._show_drop(True)
        self._render_cast()
        self._draw_timeline()
        self.status("Documento nuevo. Escribe, pega o arrastra un archivo (Ctrl+Z recupera lo borrado).")
        return True

    # --- comodidad: temporizador para dormir, temas de lectura
    READ_THEMES = {
        "neon": ("Neón (el de siempre)", SURF, TXT, CYAN, "#5d6a8c", "#c9d3f0", "#123049", False),
        "soft": ("Oscuro suave", "#16181d", "#e4e6eb", "#7dd3fc", "#7a8299", "#d6dae4", "#243447", False),
        "black": ("Negro (pantallas OLED)", "#000000", "#d9d9d9", "#67e8f9", "#6b7280", "#cfcfcf", "#1c2a3a", False),
        "sepia": ("Sepia (cálido para leer de noche)", "#f4ecd8", "#3b2f1e", "#8a4b08", "#7b6a52", "#4a3b28", "#f1d78a", True),
        "light": ("Claro", "#fbfbf8", "#1f2328", "#0b6e8a", "#6b7280", "#2b3036", "#ffe69c", True),
    }

    def apply_read_theme(self, key=None, save=True):
        key = key or self.cfg.get("read_theme", "neon")
        if key not in self.READ_THEMES:
            key = "neon"
        _n, bg, fg, title, tagl, unk, reading, light = self.READ_THEMES[key]
        self.cfg["read_theme"] = key
        self.txt.configure(bg=bg, fg=fg, insertbackground=title, selectbackground="#9cc9f5" if light else "#1d3b5c")
        self.txt.tag_configure("title", foreground=title)
        self.txt.tag_configure("tagline", foreground=tagl)
        self.txt.tag_configure("unknown", foreground=unk)
        self.txt.tag_configure("reading", background=reading)
        self._light_read = light
        self.recolor()
        if save:
            self._autosave()
            self.status("Tema de lectura: " + _n + ".")

    def _read_col(self, col):
        """En los temas claros los colores neón de los personajes se oscurecen para que se lean bien."""
        return mix(col, "#000000", 0.45) if getattr(self, "_light_read", False) else col

    def set_sleep(self, minutes=0, chapter=False):
        self._sleep = {"until": time.time() + minutes * 60 if minutes else 0, "chap": None, "armed": False, "idx": None}
        if chapter:
            ch = self._cur_chunk()
            self._sleep["chap"] = ch.chapter if ch is not None else -1
        if minutes:
            self.status(f"🌙 Temporizador: se pausa en {minutes} min, al terminar el párrafo que esté sonando.")
        elif chapter:
            self.status("🌙 Temporizador: se pausa al terminar este capítulo.")
        else:
            self.status("🌙 Temporizador apagado.")

    def _cur_chunk(self):
        p = self.play
        if not p or p.get("idx", -1) < 0 or p.get("waiting"):
            return None
        try:
            _path, chunks, _res = p["parts"][p["idx"]]
            return chunks[p["cur"]] if 0 <= p.get("cur", -1) < len(chunks) else None
        except Exception:
            return None

    def _sleep_check(self):
        sl = getattr(self, "_sleep", None)
        if not sl or not self.play or self.paused or not (sl["until"] or sl["chap"] is not None):
            return
        ch = self._cur_chunk()
        if ch is None:
            return
        if sl["chap"] is not None:
            if sl["chap"] == -1:
                sl["chap"] = ch.chapter
            elif ch.chapter != sl["chap"]:
                self._sleep_stop()
            return
        if time.time() < sl["until"]:
            return
        if not sl["armed"]:
            sl["armed"], sl["idx"] = True, id(ch)
            return
        text = getattr(self, "_segs_text", "") or ""
        new_par = id(ch) != sl["idx"] and "\n" in text[max(0, ch.src_start - 4):ch.src_start]   # empezó otro párrafo
        if new_par or time.time() - sl["until"] > 90:
            self._sleep_stop()
        elif id(ch) != sl["idx"]:
            sl["idx"] = id(ch)

    def _sleep_stop(self):
        self._sleep = None
        if self.play and not self.paused:
            self.play_toggle()
        self._autosave()
        self.status("🌙 Pausado por el temporizador. ▶ sigue donde quedó.")

    def _sleep_left(self):
        sl = getattr(self, "_sleep", None)
        if not sl:
            return ""
        if sl["chap"] is not None:
            return "  ·  🌙 fin del capítulo"
        if sl["until"]:
            left = max(0, int(sl["until"] - time.time()))
            return f"  ·  🌙 {left // 60}:{left % 60:02d}"
        return ""

    def _nw(self, key, label):
        return label + ("     ✨ nuevo" if key not in self.cfg.get("used", []) else "")

    def _use(self, key, fn):
        def run():
            if key not in self.cfg.setdefault("used", []):
                self.cfg["used"].append(key)
            fn()
        return run

    def more_menu(self):
        m = tk.Menu(self, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, activeforeground=TXT,
                    font=("Segoe UI", 10), borderwidth=0)
        m.add_command(label="📄  Documento nuevo", accelerator="Ctrl+N", command=self.clear_all)
        rec = tk.Menu(m, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, activeforeground=TXT,
                      font=("Segoe UI", 10))
        recent = [p for p in self.cfg.get("recent", []) if os.path.exists(p)]
        for p in recent:
            rec.add_command(label=os.path.basename(p), command=lambda p=p: self.load_file(p))
        if not recent:
            rec.add_command(label="(todavía no abriste ninguno)", state="disabled")
        m.add_cascade(label="🕘  Abiertos recientemente", menu=rec)
        m.add_separator()
        m.add_command(label="▶  Continuar donde lo dejé", accelerator="F7", command=self.resume_reading)
        m.add_command(label="📋  Leer lo copiado (portapapeles)", accelerator="F8", command=self.read_clipboard)
        m.add_command(label=self._nw("rep", "📋  Reemplazar todo por lo copiado"), command=self._use("rep", lambda: self.paste_doc(True)))
        m.add_command(label=self._nw("add", "➕  Añadir lo copiado al final"), command=self._use("add", lambda: self.paste_doc(False)))
        m.add_command(label="🎤  Dictar con la voz", accelerator="Win+H", command=self.dictate)
        sl = tk.Menu(m, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, activeforeground=TXT, font=("Segoe UI", 10))
        for mins in (15, 30, 45, 60, 90):
            sl.add_command(label=f"En {mins} minutos", command=lambda mm=mins: self.set_sleep(mm))
        sl.add_command(label="Al terminar el capítulo", command=lambda: self.set_sleep(chapter=True))
        sl.add_separator()
        sl.add_command(label="Apagar el temporizador", command=lambda: self.set_sleep(0))
        m.add_cascade(label=self._nw("sleep", "🌙  Temporizador para dormir"), menu=sl)
        th = tk.Menu(m, tearoff=0, bg=SURF2, fg=TXT, activebackground=CYAN_D, activeforeground=TXT, font=("Segoe UI", 10))
        for k, v in self.READ_THEMES.items():
            th.add_command(label=("● " if self.cfg.get("read_theme", "neon") == k else "   ") + v[0],
                           command=lambda k=k: self.apply_read_theme(k))
        m.add_cascade(label=self._nw("theme", "🎨  Tema de lectura"), menu=th)
        m.add_separator()
        m.add_command(label=self._nw("mark", "🔖  Marcar lo que suena"), accelerator="Ctrl+B", command=self._use("mark", self.add_mark))
        m.add_command(label=self._nw("marks", "📝  Marcadores y notas…"), accelerator="F9", command=self._use("marks", self.marks_window))
        m.add_command(label=self._nw("quiz", "🧠  Preguntas de repaso del capítulo…"), command=self._use("quiz", self.quiz_window))
        m.add_command(label="🔎  Buscar y reemplazar…", accelerator="Ctrl+H", command=lambda: self.find_replace(True))
        m.add_command(label="📚  Convertir varios documentos a audio…", command=self.batch_convert)
        m.add_command(label=self._nw("subs", "🎬  Subtitular mi video (sin internet)…"), command=self._use("subs", self.subs_window))
        m.add_separator()
        m.add_command(label="A+  Letra más grande", accelerator="Ctrl+rueda", command=lambda: self.zoom_text(1))
        m.add_command(label="A−  Letra más pequeña", accelerator="Ctrl+rueda", command=lambda: self.zoom_text(-1))
        m.add_command(label="A   Tamaño normal", accelerator="Ctrl+0", command=lambda: self.zoom_text(0))
        b = self.btn_more
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _add_recent(self, path):
        p = os.path.abspath(path)
        rec = [x for x in self.cfg.get("recent", []) if os.path.normcase(x) != os.path.normcase(p)]
        self.cfg["recent"] = ([p] + rec)[:12]

    # --- tamaño de letra del editor
    def _apply_text_size(self):
        sz = int(self.cfg.get("text_size", 13))
        self.txt.configure(font=("Segoe UI", sz))
        self.txt.tag_configure("title", font=("Bahnschrift", sz + 3, "bold"))

    def zoom_text(self, d):
        sz = 13 if d == 0 else max(9, min(30, int(self.cfg.get("text_size", 13)) + d))
        self.cfg["text_size"] = sz
        self._apply_text_size()
        self.status(f"Tamaño de letra del editor: {sz}")

    # --- continuar donde lo dejé
    def resume_reading(self):
        pos = int(getattr(self, "_resume_pos", 0) or 0)
        if not self.get_text().strip():
            self.status("No hay texto abierto.")
            return
        if pos <= 0 or pos >= len(self.get_text()):
            self.status("Todavía no escuchaste este documento: empieza desde el principio.")
            pos = 0
        else:
            try:
                self.txt.see(self._idx(pos))
            except Exception:
                pass
        self.preview_from(pos)

    # --- leer el portapapeles
    def read_clipboard(self):
        try:
            t = self.clipboard_get()
        except tk.TclError:
            t = ""
        if not t.strip():
            self.status("No hay texto copiado. Copia algo (Ctrl+C) en cualquier programa y pulsa F8.")
            return
        if self.get_text().strip() and self.get_text().strip() != t.strip():
            if not messagebox.askyesno(APP, "¿Reemplazar el texto del editor por lo que copiaste y escucharlo?\n\n"
                                            "Ctrl+Z recupera el texto anterior."):
                return
        if not self.clear_all(ask=False):
            return
        self.txt.insert("1.0", t.replace("\r\n", "\n").replace("\r", "\n"))
        self.txt.mark_set("insert", "1.0")
        self._show_drop(False)
        self.preview_from(0)          # espera a Kuon IA y empieza sola

    # --- OCR: PDF escaneados y fotos de apuntes (OCR de Windows, sin internet)
    def _ocr_then_load(self, path, kind):
        if not ocr.available():
            messagebox.showerror(APP, ("Este PDF está escaneado (sus páginas son imágenes) " if kind == "pdf" else
                                       "Es una imagen ") + "y Windows no tiene el reconocimiento de texto en español."
                                 "\n\nActívalo en Configuración > Hora e idioma > Idioma y región > Español > Opciones "
                                 "> Reconocimiento óptico de caracteres, y vuelve a abrirlo.")
            return
        what = "Este PDF está escaneado: sus páginas son imágenes." if kind == "pdf" else "Es una foto o una imagen."
        if not messagebox.askyesno(APP, f"{what}\n\n¿Reconocer el texto (OCR)? Se hace en tu PC, sin internet, en "
                                        "segundos. Revisa luego el texto: las fórmulas y la letra a mano pueden salir "
                                        "con errores."):
            return
        self._start_job("Reconociendo el texto…")

        def work():
            try:
                if kind == "pdf":
                    pages = ocr.ocr_pdf(path, cancel=self.cancel, progress=lambda d, t: self.after(
                        0, lambda: (self.pb.set(d / t), self.status(f"OCR: página {d} de {t}…"))))
                    raw = "\n\n".join(pages[i] for i in sorted(pages))
                else:
                    raw = ocr.ocr_image(path)
                text = docs.unwrap(docs.common(raw), True)
                err = None if text.strip() else RuntimeError("No se encontró texto en la imagen.")
            except Exception as e:
                text, err = "", e
                self._log_error(e)

            def done():
                self._end_job()
                if err:
                    messagebox.showerror(APP, f"No se pudo reconocer el texto:\n{err}")
                    return
                self.load_file(path, text)
                self.status(f"Texto reconocido con OCR ({len(text.split())} palabras). Revísalo antes de generar.")
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    # --- marcadores y notas mientras escuchas (idea de Kurzweil / Voice Dream)
    def _chapter_at(self, pos):
        cap = ""
        for title, a, b in self.chapters:
            if a <= pos < max(b, a + 1):
                cap = title
        return cap if cap and cap != "Texto completo" else ""

    def add_mark(self):
        text = self.get_text()
        if not text.strip():
            self.status("Abre un documento primero.")
            return
        span = self._cur_span if (self.play and getattr(self, "_cur_span", None)) else None
        if not span:
            try:
                span = (self._off("sel.first"), self._off("sel.last"))
            except tk.TclError:
                off = self._off("insert")
                a = max(text.rfind(".", 0, off), text.rfind("\n", 0, off)) + 1
                b = min([x for x in (text.find(".", off), text.find("\n", off)) if x >= 0] or [len(text)]) + 1
                span = (a, b)
        a, b = span
        snip = re.sub(r"\s+", " ", text[a:b]).strip()[:400]
        if not snip:
            return
        if any(m.get("pos") == a for m in self.marks):
            self.status("Esa frase ya estaba marcada.")
            return
        self.marks.append({"pos": a, "end": b, "text": snip, "note": "", "cap": self._chapter_at(a),
                           "t": time.strftime("%d/%m %H:%M")})
        self.marks.sort(key=lambda m: m["pos"])
        self._autosave()
        self.status(f"🔖 Marcado ({len(self.marks)}). Escribe una nota en «Marcadores y notas» (F9).")
        w = getattr(self, "_mk_win", None)
        if w is not None and w.winfo_exists():
            self._mk_render()

    def marks_window(self):
        w = getattr(self, "_mk_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            self._mk_render()
            return
        w = self._mk_win = ctk.CTkToplevel(self)
        w.title("Marcadores y notas")
        w.geometry("620x560")
        w.transient(self)
        w.configure(fg_color=SURF)
        top = ctk.CTkFrame(w, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text="Marca con Ctrl+B lo que suena; escribe tu nota; repasa con ▶.", font=font(12),
                     text_color=MUTED).pack(side="left")
        self._mk_list = ctk.CTkScrollableFrame(w, fg_color=SURF, scrollbar_button_color=SURF3)
        self._mk_list.pack(fill="both", expand=True, padx=8, pady=4)
        bot = ctk.CTkFrame(w, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=(4, 12))
        self._btn(bot, "📄 Guardar como apuntes (.md)", self._mk_export_md, width=200).pack(side="left")
        self._btn(bot, "🃏 Tarjetas para Anki", self._mk_export_anki, width=160).pack(side="left", padx=6)
        self._mk_render()

    def _mk_render(self):
        for c in self._mk_list.winfo_children():
            c.destroy()
        if not self.marks:
            ctk.CTkLabel(self._mk_list, text="Todavía no hay marcadores en este documento.", font=font(12),
                         text_color=MUTED).pack(pady=20)
            return
        lim = min(getattr(self, "_mk_limit", 40), 300)
        for i, m in enumerate(self.marks[:lim]):
            card = ctk.CTkFrame(self._mk_list, fg_color=SURF2, corner_radius=10)
            card.pack(fill="x", pady=4, padx=2)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=8, pady=(6, 0))
            ctk.CTkLabel(head, text=(m.get("cap") or "") + ("  ·  " if m.get("cap") else "") + m.get("t", ""),
                         font=font(11), text_color=CYAN).pack(side="left")
            ctk.CTkButton(head, text="✕", width=26, height=24, fg_color="transparent", hover_color=SURF3,
                          text_color=MUTED, command=lambda i=i: self._mk_del(i)).pack(side="right")
            ctk.CTkButton(head, text="▶", width=30, height=24, fg_color=SURF3, hover_color=CYAN_D,
                          command=lambda p=m["pos"]: self.preview_from(p)).pack(side="right", padx=4)
            ctk.CTkLabel(card, text=m["text"], font=font(12), text_color=TXT, wraplength=540,
                         justify="left", anchor="w").pack(fill="x", padx=10, pady=2)
            v = tk.StringVar(value=m.get("note", ""))
            e = ctk.CTkEntry(card, textvariable=v, placeholder_text="Tu nota o pregunta para repasar…",
                             fg_color=SURF3, border_color=LINE)
            e.pack(fill="x", padx=10, pady=(2, 8))
            v.trace_add("write", lambda *_a, m=m, v=v: (m.__setitem__("note", v.get()), self._autosave()))
        if len(self.marks) > lim:
            more = len(self.marks) - lim
            if lim < 300:
                ctk.CTkButton(self._mk_list, text=f"Mostrar más ({more} restantes)", fg_color=SURF3, hover_color=LINE,
                              command=lambda: (setattr(self, "_mk_limit", lim + 40), self._mk_render())).pack(fill="x", pady=6)
            else:
                ctk.CTkLabel(self._mk_list, text=f"Y {more} marcadores más: están todos en «Guardar apuntes».",
                             font=font(11), text_color=MUTED).pack(pady=6)

    def _mk_del(self, i):
        if 0 <= i < len(self.marks):
            self.marks.pop(i)
            self._autosave()
            self._mk_render()

    def _mk_export_md(self):
        if not self.marks:
            return
        base = os.path.splitext(self.doc_key)[0] if self.doc_path else "apuntes"
        p = filedialog.asksaveasfilename(title="Guardar apuntes", defaultextension=".md",
                                         initialfile=self._safe_name(base) + " - apuntes.md",
                                         filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")])
        if not p:
            return
        lines = [f"# Apuntes · {base}", ""]
        cap = None
        for m in self.marks:
            if m.get("cap") and m["cap"] != cap:
                cap = m["cap"]
                lines += ["", f"## {cap}", ""]
            lines.append(f"- {m['text']}" + (f"\n  - **Nota:** {m['note']}" if m.get("note") else ""))
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.status(f"Apuntes guardados: {os.path.basename(p)}")

    def _mk_export_anki(self):
        cards = [(m["note"], m["text"]) for m in self.marks if m.get("note")]
        if not cards:
            messagebox.showinfo(APP, "Escribe una nota o pregunta en los marcadores: la nota será el frente de la "
                                     "tarjeta y la frase marcada, la respuesta.", parent=self._mk_win)
            return
        self._save_anki(cards, "marcadores", parent=self._mk_win)

    def _save_anki(self, cards, what, parent=None):
        """Archivo de texto que Anki importa (Archivo > Importar): frente <TAB> reverso."""
        base = os.path.splitext(self.doc_key)[0] if self.doc_path else "estudio"
        p = filedialog.asksaveasfilename(title="Guardar tarjetas para Anki", defaultextension=".txt",
                                         initialfile=self._safe_name(base) + f" - anki {what}.txt",
                                         filetypes=[("Texto para Anki", "*.txt")], parent=parent)
        if not p:
            return
        clean = lambda s: re.sub(r"\s+", " ", s or "").replace("\t", " ").strip()
        with open(p, "w", encoding="utf-8") as f:
            f.write("#separator:tab\n#html:false\n#tags column:3\n")
            for front, back in cards:
                f.write(f"{clean(front)}\t{clean(back)}\t{self._safe_name(base).replace(' ', '_')}\n")
        self.status(f"{len(cards)} tarjetas guardadas para Anki: {os.path.basename(p)}")

    # --- preguntas de repaso con IA («pausa y responde»)
    def quiz_window(self):
        text = self.get_text()
        if not text.strip():
            self.status("Abre un documento primero.")
            return
        w = getattr(self, "_qz_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        w = self._qz_win = ctk.CTkToplevel(self)
        w.title("Preguntas de repaso")
        w.geometry("640x600")
        w.transient(self)
        w.configure(fg_color=SURF)
        caps = [c for c in self.chapters if c[2] > c[1]] or [("Texto completo", 0, len(text))]
        self._qz_caps = caps
        here = self._off("insert")
        cur = next((c[0] for c in caps if c[1] <= here < c[2]), caps[0][0])
        top = ctk.CTkFrame(w, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 4))
        self._qz_cap = tk.StringVar(value=cur)
        ctk.CTkComboBox(top, variable=self._qz_cap, values=[c[0] for c in caps][:300], width=330, state="readonly",
                        fg_color=SURF2, border_color=LINE, button_color=SURF3).pack(side="left")
        self._qz_btn = self._btn(top, "✨ Preguntas", self._qz_generate, width=110)
        self._qz_btn.pack(side="left", padx=6)
        self._btn(top, "▶ Escuchar y luego preguntar", self._qz_listen_then_ask, width=200).pack(side="left")
        ctk.CTkLabel(w, text="La IA (la clave del Asistente) prepara preguntas con una cita del texto; la app comprueba "
                             "que la cita existe antes de mostrarla. Ponerse a prueba es la forma de estudiar con más "
                             "evidencia.", font=font(11), text_color=MUTED, wraplength=600, justify="left").pack(
            anchor="w", padx=12)
        self._qz_q = ctk.CTkLabel(w, text="", font=font(15, "bold"), text_color=TXT, wraplength=600, justify="left",
                                  anchor="w")
        self._qz_q.pack(fill="x", padx=14, pady=(14, 6))
        self._qz_opts = ctk.CTkFrame(w, fg_color="transparent")
        self._qz_opts.pack(fill="x", padx=12)
        self._qz_fb = ctk.CTkLabel(w, text="", font=font(12), text_color=MUTED, wraplength=600, justify="left",
                                   anchor="w")
        self._qz_fb.pack(fill="x", padx=14, pady=8)
        nav = ctk.CTkFrame(w, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(0, 12), side="bottom")
        self._btn(nav, "▶ Escuchar esa parte", self._qz_listen_cite, width=150).pack(side="left")
        self._btn(nav, "🃏 Guardar para Anki", self._qz_anki, width=150).pack(side="left", padx=6)
        self._btn(nav, "Siguiente ▶", lambda: self._qz_show(self._qz_i + 1), width=110).pack(side="right")
        self._qz_items, self._qz_i, self._qz_score = [], 0, [0, 0]
        self._qz_show(0)

    def _qz_range(self):
        c = next((c for c in self._qz_caps if c[0] == self._qz_cap.get()), self._qz_caps[0])
        return c[1], c[2]

    def _qz_generate(self, after=None):
        try:
            c = self._ai_conf()
        except Exception:
            c = {}
        if not c or not c.get("url") or not c.get("model") or (c.get("needs_key") and not c.get("key")):
            messagebox.showinfo(APP, "Para generar preguntas, configura primero el Asistente (pestaña «Asistente» > "
                                     "Configurar) con tu clave de Gemini, Groq u otro servicio.", parent=self._qz_win)
            return
        a, b = self._qz_range()
        text = self.get_text()[a:b]
        chunk = text[:14000]
        self._qz_btn.configure(state="disabled", text="Pensando…")
        self._qz_q.configure(text="La IA está leyendo el capítulo y preparando preguntas…")

        def work():
            try:
                reply = ai.chat(c["url"], c["key"], c["model"], repaso.prompt(chunk), temperature=0.3, timeout=150,
                                fallback=c.get("fallback", ""))
                items, bad = repaso.parse(reply, chunk)
                err = None
            except Exception as e:
                items, bad, err = [], 0, e
                self._log_error(e)

            def done():
                if not self._qz_win.winfo_exists():
                    return
                self._qz_btn.configure(state="normal", text="✨ Preguntas")
                if err:
                    self._qz_q.configure(text=f"La IA no respondió: {err}")
                    return
                for q in items:
                    q["off"] = a
                self._qz_items, self._qz_score = items, [0, 0]
                note = f" (descarté {bad} sin cita comprobable)" if bad else ""
                self.status(f"{len(items)} preguntas listas{note}.")
                self._qz_show(0)
                if after:
                    after()
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _qz_show(self, i):
        for b in self._qz_opts.winfo_children():
            b.destroy()
        self._qz_fb.configure(text="")
        if not self._qz_items:
            self._qz_q.configure(text="Elige el capítulo y pulsa «✨ Preguntas».")
            return
        if i >= len(self._qz_items):
            ok, tot = self._qz_score
            self._qz_q.configure(text=f"¡Terminaste! Acertaste {ok} de {tot}. Repasa las que fallaste con "
                                      "«▶ Escuchar esa parte», y vuelve a preguntarte en unos días.")
            self._qz_i = len(self._qz_items)
            return
        self._qz_i = i
        q = self._qz_items[i]
        self._qz_q.configure(text=f"{i + 1}/{len(self._qz_items)}.  {q['pregunta']}")
        for k, o in enumerate(q["opciones"]):
            ctk.CTkButton(self._qz_opts, text=f"{'ABCDEF'[k]}) {o}", anchor="w", height=34, fg_color=SURF2,
                          hover_color=SURF3, text_color=TXT, font=font(12),
                          command=lambda k=k: self._qz_answer(k)).pack(fill="x", pady=3)

    def _qz_answer(self, k):
        q = self._qz_items[self._qz_i]
        if q.get("done"):
            return
        q["done"] = True
        right = k == q["correcta"]
        self._qz_score[0] += 1 if right else 0
        self._qz_score[1] += 1
        btns = self._qz_opts.winfo_children()
        for j, b in enumerate(btns):
            if j == q["correcta"]:
                b.configure(fg_color="#14532d")
            elif j == k:
                b.configure(fg_color="#7f1d1d")
        self._qz_fb.configure(text=("✓ ¡Correcto!" if right else "✗ No. La correcta es "
                                    f"{'ABCDEF'[q['correcta']]}.") + f"\nEl texto dice: «{q['cita']}»",
                              text_color=GREEN if right else AMBER)

    def _qz_listen_cite(self):
        if self._qz_items and self._qz_i < len(self._qz_items):
            q = self._qz_items[self._qz_i]
            text = self.get_text()
            a, b = self._qz_range()
            p = repaso.cite_pos(text[a:b], q["cita"])
            if p >= 0:
                self.preview_from(a + p)

    def _qz_listen_then_ask(self):
        """«Pausa y responde»: escucha el capítulo y, al terminar, salen las preguntas."""
        a, b = self._qz_range()
        if not self._qz_items:
            self._qz_generate()
        self.preview_range(a, b)
        self.status("Escuchando el capítulo; al terminar aparecen las preguntas.")
        self._qz_wait = time.time()

        def check():
            w = getattr(self, "_qz_win", None)
            if w is None or not w.winfo_exists():
                return
            if self.play or time.time() - self._qz_wait < 4:
                self.after(1000, check)
                return
            w.deiconify()
            w.lift()
            self._qz_show(0)
        self.after(4000, check)

    def _qz_anki(self):
        if not self._qz_items:
            return
        cards = [(q["pregunta"], q["opciones"][q["correcta"]] + " — «" + q["cita"] + "»") for q in self._qz_items]
        self._save_anki(cards, "preguntas", parent=self._qz_win)

    # --- dictar (como el botón «Dictar» del teléfono): usa el dictado de Windows (Win+H)
    def dictate(self):
        self._show_drop(False)
        self.txt.focus_set()
        self.txt.mark_set("insert", "end-1c")
        self.txt.see("insert")
        self.status("Habla y Windows lo escribe en el editor. Si no se abre, actívalo en Configuración > "
                    "Hora e idioma > Voz. Vuelve a pulsar Win+H para terminar.")

        def press():
            try:
                import ctypes
                u = ctypes.windll.user32
                VK_LWIN, VK_H, UP = 0x5B, 0x48, 0x0002
                u.keybd_event(VK_LWIN, 0, 0, 0)
                u.keybd_event(VK_H, 0, 0, 0)
                u.keybd_event(VK_H, 0, UP, 0)
                u.keybd_event(VK_LWIN, 0, UP, 0)
            except Exception as e:
                self._log_error(e)
        self.after(150, press)

    # --- buscar y reemplazar
    def find_replace(self, replace=True):
        w = getattr(self, "_find_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            self._find_entry.focus_set()
            return
        w = self._find_win = ctk.CTkToplevel(self)
        w.title("Buscar y reemplazar")
        w.geometry("430x210")
        w.resizable(False, False)
        w.transient(self)
        w.configure(fg_color=SURF)
        self._v_find, self._v_repl = tk.StringVar(), tk.StringVar()
        self._v_case = tk.BooleanVar(value=False)
        try:
            sel = self.txt.get("sel.first", "sel.last")
            if sel and "\n" not in sel and len(sel) < 80:
                self._v_find.set(sel)
        except tk.TclError:
            pass
        g = ctk.CTkFrame(w, fg_color="transparent")
        g.pack(fill="both", expand=True, padx=14, pady=12)
        ctk.CTkLabel(g, text="Buscar", font=font(12), text_color=MUTED).grid(row=0, column=0, sticky="w")
        self._find_entry = ctk.CTkEntry(g, textvariable=self._v_find, width=300, fg_color=SURF2, border_color=LINE)
        self._find_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        ctk.CTkLabel(g, text="Cambiar por", font=font(12), text_color=MUTED).grid(row=1, column=0, sticky="w",
                                                                                   padx=(0, 8))
        ctk.CTkEntry(g, textvariable=self._v_repl, width=300, fg_color=SURF2, border_color=LINE).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ctk.CTkCheckBox(g, text="Distinguir mayúsculas", variable=self._v_case, font=font(12),
                        fg_color=CYAN).grid(row=2, column=1, columnspan=3, sticky="w", pady=4)
        self._btn(g, "Siguiente (F3)", self.find_next, width=110).grid(row=3, column=1, pady=(8, 0), sticky="w")
        self._btn(g, "Cambiar", self._replace_one, width=80).grid(row=3, column=2, pady=(8, 0))
        self._btn(g, "Cambiar todo", self._replace_all, width=100).grid(row=3, column=3, pady=(8, 0), sticky="e")
        self._lbl_find = ctk.CTkLabel(g, text="", font=font(11), text_color=MUTED)
        self._lbl_find.grid(row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self._find_entry.bind("<Return>", lambda e: self.find_next())
        w.bind("<Escape>", lambda e: (self.txt.tag_remove("found", "1.0", "end"), w.withdraw()))
        self.txt.tag_configure("found", background="#5a4a12")
        self.after(80, lambda: self._find_entry.winfo_exists() and self._find_entry.focus_set())

    def find_next(self):
        q = getattr(self, "_v_find", None)
        if q is None or not q.get():
            return self.find_replace(False)
        s = q.get()
        nocase = not self._v_case.get()
        start = self.txt.index("insert")
        try:
            if self.txt.compare("sel.first", "==", "insert") or self.txt.tag_ranges("found"):
                start = self.txt.index("insert +1c")
        except tk.TclError:
            pass
        pos = self.txt.search(s, start, "end", nocase=nocase) or self.txt.search(s, "1.0", "end", nocase=nocase)
        self.txt.tag_remove("found", "1.0", "end")
        self.txt.tag_remove("sel", "1.0", "end")
        if not pos:
            self._lbl_find.configure(text="No se encontró.")
            return
        end = f"{pos}+{len(s)}c"
        self.txt.tag_add("found", pos, end)
        self.txt.tag_add("sel", pos, end)
        self.txt.mark_set("insert", pos)
        self.txt.see(pos)
        n = len(re.findall(re.escape(s), self.get_text(), 0 if not nocase else re.I))
        self._lbl_find.configure(text=f"{n} coincidencia(s) en el texto.")

    def _replace_one(self):
        r = self.txt.tag_ranges("found")
        if not r:
            return self.find_next()
        self._one_undo_step(lambda: (self.txt.delete(r[0], r[1]), self.txt.insert(r[0], self._v_repl.get())))
        self.find_next()

    def _one_undo_step(self, fn):
        """Borrar + escribir como un solo paso: un Ctrl+Z lo deshace entero."""
        self.txt.configure(autoseparators=False)
        try:
            self.txt.edit_separator()
            fn()
            self.txt.edit_separator()
        finally:
            self.txt.configure(autoseparators=True)

    def _replace_all(self):
        s, rep = self._v_find.get(), self._v_repl.get()
        if not s:
            return
        flags = 0 if self._v_case.get() else re.I
        text = self.get_text()
        new, n = re.subn(re.escape(s), lambda m: rep, text, flags=flags)
        if not n:
            self._lbl_find.configure(text="No se encontró.")
            return
        y = self.txt.yview()[0]
        self._one_undo_step(lambda: (self.txt.delete("1.0", "end"), self.txt.insert("1.0", new)))
        self.txt.focus_set()
        self.txt.yview_moveto(y)
        self._lbl_find.configure(text=f"Se cambiaron {n}. Ctrl+Z lo deshace.")
        self.status(f"Buscar y reemplazar: {n} cambio(s).")

    # --- convertir varios documentos (como el conversor por lotes de Balabolka)
    def batch_convert(self):
        if self.busy:
            self.status("Hay un trabajo en curso. Espera a que termine o pulsa Cancelar.")
            return
        paths = filedialog.askopenfilenames(title="Elige los documentos que quieres pasar a audio",
                                            filetypes=[("Documentos", " ".join("*" + e for e in docs.SUPPORTED)),
                                                       ("Todos", "*.*")])
        if not paths:
            return
        outdir = self.v_outdir.get()
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP, f"Carpeta de destino no válida:\n{e}")
            return
        fmt = self.v_fmt.get().lower()
        if not messagebox.askyesno(APP, f"Se generará un audio {fmt.upper()} por cada uno de los {len(paths)} "
                                        f"documentos, en:\n{outdir}\n\nCon tus voces y ajustes actuales. "
                                        "Puedes cancelar cuando quieras. ¿Empezar?"):
            return
        self._store()
        self.stop_all()
        import dataclasses
        st0, workers = self._settings(), int(self.v_workers.get())
        lang, narr, variant = self.v_lang.get(), self._vname(self.v_narr.get()), self.v_variant.get()
        th, voices = float(self.v_threshold.get()), list(self.voices)
        projects, docs_cfg = self.cfg.get("projects", {}), self.cfg.get("docs", {})
        mem0 = json.dumps(self.memory)
        n = len(paths)
        self._start_job(f"Convirtiendo {n} documentos…")

        def work():
            done, fails, t0 = [], [], time.time()
            for k, p in enumerate(paths):
                if self.cancel.is_set():
                    break
                name = os.path.basename(p)
                self.after(0, lambda k=k, name=name: self.status(f"[{k + 1}/{n}] Kuon IA está leyendo {name}…"))
                try:
                    text = docs.read_document(p)
                    proj = project_of(p)
                    pj, ds = projects.get(proj, {}), docs_cfg.get(name, {})
                    mem = json.loads(mem0)
                    brain.merge_base(mem, BASE_MEMORY, proj)
                    segs, chars, _ = brain.analyze(text, ds.get("overrides", {}),
                                                   pj.get("aliases") or ds.get("aliases", {}), memory=mem,
                                                   project=proj, threshold=th, doc=name)
                    cast = vx.auto_cast(list(chars.values()), voices, lang, narr, dict(pj.get("cast") or {}),
                                        neutral=variant)
                    st = dataclasses.replace(st0, cast=json.loads(json.dumps(cast)), pron=dict(pj.get("pron", {})),
                                             emo_over=dict(ds.get("emo", {})))
                    chunks = audio.plan(text, segs, docs.split_chapters(text), st)
                    if not chunks:
                        raise ValueError("no tiene texto para narrar")
                    out = os.path.join(outdir, self._safe_name(os.path.splitext(name)[0]) + "." + fmt)

                    def prog(d, t, k=k, name=name):
                        self.after(0, lambda: (self.pb.set((k + d / max(t, 1)) / n),
                                               self.status(f"[{k + 1}/{n}] {name}: fragmento {d}/{t}")))
                    audio.render(chunks, out, st, fmt, cancel=self.cancel, workers=workers, progress=prog)
                    done.append(out)
                except audio.Cancelled:
                    break
                except Exception as e:
                    self._log_error(e)
                    fails.append(f"{name}: {e}")
            secs = int(time.time() - t0)

            def finish():
                self._end_job()
                self.pb.set(1 if done else 0)
                msg = f"Listo: {len(done)} de {n} audios en {outdir} ({secs // 60} min {secs % 60} s)."
                if self.cancel.is_set():
                    msg = f"Cancelado. Se terminaron {len(done)} de {n} audios."
                self.status(msg)
                if fails:
                    messagebox.showwarning(APP, msg + "\n\nNo se pudieron convertir:\n" + "\n".join(fails[:10]))
                elif done and messagebox.askyesno(APP, msg + "\n\n¿Abrir la carpeta?"):
                    self.open_outdir()
            self.after(0, finish)
        threading.Thread(target=work, daemon=True).start()

    def _set_line_emotion(self, seg, emo):
        self._checkpoint("emoción de una línea")
        if emo:
            self.emo_over[seg.key] = emo
        else:
            self.emo_over.pop(seg.key, None)
        st = self._settings()
        one = audio.build_chunks(self.segs, st, seg.start, seg.start + 1)
        self.status(f"Emoción de la línea: {dict(EMO_CHOICES).get(emo, 'Automática')}")
        self.schedule_plan()
        if one:
            self._stream(one, st, "Probando emoción")

    def _override(self, seg, name, learn=True):
        if name:
            name = self._canon_name(name)
        self._checkpoint(f"diálogo para {name or 'el narrador'}")
        if name:
            self._allow_name(name)
        self.overrides[seg.key] = name
        learned = brain.learn_from_correction(self.memory, self.project, self.get_text(), self.segs, seg, name) \
            if learn else []
        save_memory(self.memory)
        self._update_memory_label()
        if learned:
            self.status("Kuon IA aprendió: " + ", ".join(learned))
        self.schedule_analysis(10)

    def _override_new(self, seg):
        d = ctk.CTkInputDialog(text="¿Quién dice este diálogo?", title="Nuevo personaje", fg_color=SURF,
                               button_fg_color=CYAN_D, entry_fg_color=SURF2)
        nm = (d.get_input() or "").strip()
        if nm:
            self._override(seg, nm)

    # ------------------------------------------------------------ revisar dudosos (idea de ElevenLabs / BookNLP)
    def _doubt_keys(self):
        med = bool(getattr(self, "_rv_medium", None) and self._rv_medium.get())
        return [s.key for s in self.segs if s.key and ia_dudosos.is_doubtful(s, med)]

    def _seg_by_key(self, key):
        return next((s for s in self.segs if s.key == key), None)

    def review_doubts(self):
        if not self.segs:
            self.status("Abre un documento primero: Kuon IA necesita leerlo.")
            return
        w = getattr(self, "_rv_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        self._rv_ai = getattr(self, "_rv_ai", {}) if getattr(self, "_rv_doc", None) == self.doc_key else {}
        self._rv_doc = self.doc_key
        self._rv_done = set()
        w = self._rv_win = ctk.CTkToplevel(self)
        w.title("Revisar diálogos dudosos")
        w.geometry("640x560")
        w.transient(self)
        w.configure(fg_color=SURF)
        top = ctk.CTkFrame(w, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        self._rv_medium = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(top, text="Incluir los de confianza media", variable=self._rv_medium, font=font(12),
                        fg_color=CYAN, command=lambda: self._rv_reload(0)).pack(side="left")
        self._rv_btn_ai = self._btn(top, "✨ Pedir ayuda a la IA", self._rv_ask_ai, width=150)
        self._rv_btn_ai.pack(side="right")
        self._rv_btn_all = self._btn(top, "✓ Aceptar todas las de la IA", self._rv_accept_all_ai, width=170)
        self._rv_lbl_head = ctk.CTkLabel(w, text="", font=font(13, "bold"), text_color=TXT, anchor="w",
                                         justify="left", wraplength=600)
        self._rv_lbl_head.pack(fill="x", padx=14, pady=(6, 2))
        self._rv_txt = tk.Text(w, wrap="word", height=9, bg=SURF2, fg=MUTED, relief="flat", font=("Segoe UI", 12),
                               padx=12, pady=8, highlightthickness=0)
        self._rv_txt.pack(fill="both", expand=True, padx=14, pady=4)
        self._rv_txt.tag_configure("cur", foreground=TXT, background="#123049")
        self._rv_lbl_ai = ctk.CTkLabel(w, text="", font=font(12), text_color=AMBER, anchor="w")
        self._rv_lbl_ai.pack(fill="x", padx=14)
        ctk.CTkLabel(w, text="¿Quién lo dice?", font=font(12), text_color=MUTED, anchor="w").pack(fill="x", padx=14)
        self._rv_who = ctk.CTkFrame(w, fg_color="transparent")
        self._rv_who.pack(fill="x", padx=10, pady=(2, 6))
        nav = ctk.CTkFrame(w, fg_color="transparent")
        nav.pack(fill="x", padx=14, pady=(2, 12))
        self._btn(nav, "◀ Anterior", lambda: self._rv_go(-1), width=90).pack(side="left")
        self._btn(nav, "▶ Escuchar", self._rv_listen, width=90).pack(side="left", padx=6)
        self._btn(nav, "Siguiente ▶", lambda: self._rv_go(1), width=100).pack(side="right")
        self._rv_lbl_n = ctk.CTkLabel(nav, text="", font=font(12), text_color=MUTED)
        self._rv_lbl_n.pack(side="right", padx=10)
        w.bind("<Escape>", lambda e: w.destroy())
        for i in range(1, 10):
            w.bind(str(i), lambda e, n=i: self._rv_pick_index(n - 1))
        w.bind("<Right>", lambda e: self._rv_go(1))
        w.bind("<Left>", lambda e: self._rv_go(-1))
        self._rv_reload(0)

    def _rv_reload(self, idx=None):
        self._rv_keys = self._doubt_keys()
        if idx is not None:
            self._rv_i = idx
        self._rv_show()

    def _rv_show(self):
        w = getattr(self, "_rv_win", None)
        if w is None or not w.winfo_exists():
            return
        keys = self._rv_keys
        for b in self._rv_who.winfo_children():
            b.destroy()
        self._rv_txt.configure(state="normal")
        self._rv_txt.delete("1.0", "end")
        if not keys:
            self._rv_lbl_head.configure(text="No quedan diálogos dudosos. ✓")
            self._rv_lbl_n.configure(text="")
            self._rv_lbl_ai.configure(text="")
            self._rv_txt.configure(state="disabled")
            return
        self._rv_i = max(0, min(self._rv_i, len(keys) - 1))
        seg = self._seg_by_key(keys[self._rv_i])
        if seg is None:
            self._rv_txt.configure(state="disabled")
            return
        text = self.get_text()
        a, b = seg.start, seg.end
        lo = text.rfind("\n", 0, max(0, a - 450)) + 1 if a > 450 else 0
        hi = text.find("\n", min(len(text), b + 150))
        hi = len(text) if hi < 0 else hi
        self._rv_txt.insert("end", ("…" if lo else "") + text[lo:a])
        self._rv_txt.insert("end", text[a:b], "cur")
        self._rv_txt.insert("end", text[b:hi] + ("…" if hi < len(text) else ""))
        self._rv_txt.see("cur.first")
        self._rv_txt.configure(state="disabled")
        who = seg.speaker or (f"narrador (dudaba: ¿{seg.guess}?)" if seg.guess else "narrador")
        done = " · ✓ ya revisado" if seg.key in self._rv_done else ""
        self._rv_lbl_head.configure(text=f"Kuon IA dice: {who}  ·  confianza {int(round(seg.conf * 100))} %"
                                         + (f"  ·  por {seg.source}" if seg.source else "") + done)
        self._rv_lbl_n.configure(text=f"{self._rv_i + 1} de {len(keys)}")
        sug = self._rv_ai.get(seg.key)
        if sug is not None:
            self._rv_lbl_ai.configure(text=f"✨ La IA cree que lo dice: {sug or 'el narrador'}")
        else:
            self._rv_lbl_ai.configure(text="")
        try:        # también se marca en el editor
            self.txt.tag_remove("sel", "1.0", "end")
            self.txt.tag_add("sel", self._idx(a), self._idx(b))
            self.txt.see(self._idx(a))
        except Exception:
            pass
        cands = []
        for nm in ([sug] if sug else []) + ([seg.guess] if seg.guess else []) + ([seg.speaker] if seg.speaker else []) \
                + [n for n in self.chars]:
            if nm and nm not in cands:
                cands.append(nm)
        cands = cands[:9]
        self._rv_cands = cands + [""]
        row = None
        for i, nm in enumerate(self._rv_cands):
            if i % 4 == 0:
                row = ctk.CTkFrame(self._rv_who, fg_color="transparent")
                row.pack(fill="x", pady=2)
            col = self.char_color.get(nm, TXT) if nm else AMBER
            label = (f"{i + 1}. " if i < 9 else "") + (nm or "Narrador / grupo") + (" ✨" if nm and nm == sug else "")
            ctk.CTkButton(row, text=label, height=30, width=140, fg_color=SURF3, hover_color=CYAN_D, text_color=col,
                          font=font(12), command=lambda n=nm: self._rv_pick(n)).pack(side="left", padx=3)
        ctk.CTkButton(row, text="Otro…", height=30, width=80, fg_color=SURF3, hover_color=CYAN_D, font=font(12),
                      command=lambda: self._rv_pick(None)).pack(side="left", padx=3)

    def _rv_pick_index(self, i):
        if 0 <= i < len(getattr(self, "_rv_cands", [])):
            self._rv_pick(self._rv_cands[i])

    def _rv_pick(self, name):
        if not self._rv_keys:
            return
        seg = self._seg_by_key(self._rv_keys[self._rv_i])
        if seg is None:
            return
        if name is None:
            d = ctk.CTkInputDialog(text="¿Quién dice este diálogo?", title="Otro personaje", fg_color=SURF,
                                   button_fg_color=CYAN_D, entry_fg_color=SURF2)
            name = (d.get_input() or "").strip()
            if not name:
                return
        self._rv_done.add(seg.key)
        self._override(seg, name)          # Kuon IA aprende de cada respuesta
        self._rv_go(1)

    def _rv_go(self, d):
        if self._rv_keys:
            self._rv_i = (self._rv_i + d) % len(self._rv_keys)
        self._rv_show()

    def _rv_listen(self):
        if self._rv_keys:
            seg = self._seg_by_key(self._rv_keys[self._rv_i])
            if seg:
                self.preview_range(seg.start, seg.end)

    def _rv_after_analysis(self):
        """Tras cada corrección Kuon IA vuelve a leer: la lista se actualiza sin perder el lugar."""
        w = getattr(self, "_rv_win", None)
        if w is None or not w.winfo_exists():
            return
        cur = self._rv_keys[self._rv_i] if self._rv_keys and self._rv_i < len(self._rv_keys) else None
        keys = self._doubt_keys()
        keys = [k for k in keys if k not in self._rv_done] + [k for k in self._rv_keys if k in self._rv_done
                                                             and self._seg_by_key(k)]
        self._rv_keys = keys
        if cur in keys and cur not in self._rv_done:
            self._rv_i = keys.index(cur)
        self._rv_show()

    # --- ayuda de IA (usa el proveedor y la clave del Asistente)
    def _rv_ask_ai(self):
        try:
            c = self._ai_conf()
        except Exception:
            c = {}
        if not c or not c.get("url") or not c.get("model") or (c.get("needs_key") and not c.get("key")):
            messagebox.showinfo(APP, "Para pedir ayuda a la IA, primero configura el Asistente (pestaña «Asistente» > "
                                     "Configurar) con tu clave de Gemini, Groq, OpenRouter u otro servicio. "
                                     "Gemini y Groq tienen claves gratis.", parent=self._rv_win)
            return
        segs = [self._seg_by_key(k) for k in self._rv_keys]
        items = [(s.key, s.start, s.end) for s in segs if s is not None][:150]
        if not items:
            return
        text, chars, aliases = self.get_text(), dict(self.chars), dict(self.aliases)
        self._rv_btn_ai.configure(state="disabled", text="✨ Preguntando…")
        self.status(f"Preguntando a la IA por {len(items)} diálogos dudosos (solo se envían esos trozos)…")

        def chat_fn(msgs):
            return ai.chat(c["url"], c["key"], c["model"], msgs, temperature=0.1, timeout=120,
                           fallback=c.get("fallback", ""))

        def work():
            try:
                res = ia_dudosos.suggest(text, items, chars, aliases, chat_fn,
                                         on_progress=lambda i, n: self.after(0, lambda: self.status(
                                             f"IA: revisando el bloque {i} de {n}…")))
                err = None
            except Exception as e:
                res, err = {}, e
                self._log_error(e)

            def done():
                self._rv_ai.update(res)
                if self._rv_btn_ai.winfo_exists():
                    self._rv_btn_ai.configure(state="normal", text="✨ Pedir ayuda a la IA")
                if err:
                    messagebox.showerror(APP, f"La IA no respondió: {err}", parent=self._rv_win)
                    return
                agree = sum(1 for k, v in res.items() if (self._seg_by_key(k) or None) and
                            (self._seg_by_key(k).speaker or "") == v)
                self.status(f"La IA respondió {len(res)} de {len(items)}. Coincide con Kuon IA en {agree}; "
                            "las demás aparecen con ✨ para que decidas.")
                if res and self._rv_win.winfo_exists() and not self._rv_btn_all.winfo_manager():
                    self._rv_btn_all.pack(side="right", padx=6)
                self._rv_show()
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _rv_accept_all_ai(self):
        todo = [(k, v) for k, v in self._rv_ai.items() if k in self._rv_keys and k not in self._rv_done]
        if not todo:
            return
        if not messagebox.askyesno(APP, f"¿Aplicar las {len(todo)} sugerencias de la IA?\n\nKuon IA aprenderá de ellas. "
                                        "Puedes deshacer con ↶.", parent=self._rv_win):
            return
        self._checkpoint("sugerencias de la IA")
        for k, v in todo:
            seg = self._seg_by_key(k)
            if seg is None:
                continue
            name = self._canon_name(v) if v else ""
            if name:
                self._allow_name(name)
            self.overrides[seg.key] = name
            self._rv_done.add(k)
        save_memory(self.memory)
        self.schedule_analysis(10)
        self.status(f"Se aplicaron {len(todo)} sugerencias de la IA.")

    def _update_memory_label(self):
        m = self.memory
        n_ep = sum(len(v) for v in m.get("epithets", {}).values())
        if hasattr(self, "lbl_memory"):
            st = m.get("style", {}).get(self.project, {})
            names = {n for d in st.values() for n in d}
            lines = sum(x["n"] for d in st.values() for x in d.values())
            cal = m.get("calib", {}).get(self.project, {})
            bad = [k for k, v in cal.items() if v[1] > v[0]]
            self.lbl_memory.configure(
                text=f"Estilo aprendido de {len(names)} personajes ({lines} líneas en {len(st)} capítulos de esta obra) · "
                     + (f"desconfía de: {', '.join(bad)} · " if bad else "") +
                     f"{len(m.get('aliases', {}))} apodos/alias · {len(m.get('genders', {}))} géneros corregidos · "
                     + (f"precargado de tus obras: {len(BASE_MEMORY.get('genders', {}))} personajes y "
                        f"{len(BASE_MEMORY.get('style', {}))} obras · " if BASE_MEMORY else "") +
                     f"{len(m.get('not_names', []))} palabras que no son personajes · {n_ep} epítetos "
                     f"(«el demonio» = …) · {len(m.get('verbs', []))} verbos de habla nuevos · "
                     f"{m.get('stats', {}).get('corrections', 0)} correcciones tuyas")

    def show_memory(self):
        m = self.memory
        lines = ["APODOS", *[f"  {a} = {b}" for a, b in m.get("aliases", {}).items()],
                 "", "GÉNEROS", *[f"  {a}: {'mujer' if g == 'F' else 'hombre'}" for a, g in m.get("genders", {}).items()],
                 "", "NO SON PERSONAJES", "  " + ", ".join(m.get("not_names", [])),
                 "", "EPÍTETOS", *[f"  [{p}] {e} = {n}" for p, d in m.get("epithets", {}).items() for e, n in d.items()],
                 "", "VERBOS DE HABLA APRENDIDOS", "  " + ", ".join(m.get("verbs", []))]
        w = ctk.CTkToplevel(self, fg_color=SURF)
        w.title("Memoria de Kuon IA")
        w.geometry("520x560")
        tb = ctk.CTkTextbox(w, fg_color=SURF2, text_color=TXT, font=("Consolas", 12))
        tb.pack(fill="both", expand=True, padx=10, pady=10)
        tb.insert("1.0", "\n".join(lines))
        tb.configure(state="disabled")

    def forget_memory(self):
        if messagebox.askyesno(APP, "¿Borrar todo lo que Kuon IA aprendió de tus correcciones?"):
            self.memory = brain.new_memory()
            save_memory(self.memory)
            self._update_memory_label()
            self.schedule_analysis(10)

    def _render_chapters(self):
        for w in self.ch_list.winfo_children():
            w.destroy()
        if len(self.chapters) > 150:
            ctk.CTkLabel(self.ch_list, text=f"Se muestran 150 de {len(self.chapters)} capítulos (usa la lista de capítulos "
                                            "de arriba del editor para ir a los demás).", font=font(11), text_color=MUTED,
                         wraplength=330, justify="left").pack(anchor="w", pady=(0, 4))
        for i, (title, a, b) in enumerate(self.chapters[:150]):
            row = ctk.CTkFrame(self.ch_list, fg_color=SURF2, corner_radius=8)
            row.pack(fill="x", pady=2)
            ctk.CTkButton(row, text=title[:46], anchor="w", fg_color="transparent", hover_color=SURF3,
                          text_color=TXT, font=font(12), height=26,
                          command=lambda a=a: (self.txt.see(self._idx(a)), self.txt.mark_set("insert", self._idx(a)))
                          ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="▶", width=28, height=24, fg_color=SURF3, hover_color=CYAN_D,
                          command=lambda a=a, b=b: self.preview_range(a, b)).pack(side="right", padx=4)

    # ------------------------------------------------------------ reproducción
    # Reproducción en streaming: se sintetiza por partes y cada parte suena en cuanto está lista.
    # Lo escuchado queda en caché, así que al guardar no se vuelve a pedir nada a Microsoft.
    def play_toggle(self):
        if self.play and AUDIO_OK:
            if self.paused:
                pygame.mixer.music.unpause()
                self.play["part_t0"] += time.time() - self.play["paused_at"]
                self.paused = False
                self.btn_play.configure(text="❚❚")
            elif pygame.mixer.music.get_busy() or self.play.get("waiting"):
                pygame.mixer.music.pause()
                self.play["paused_at"] = time.time()
                self.paused = True
                self.btn_play.configure(text="▶")
            else:
                self.preview()
            return
        self.preview()

    def preview(self):
        try:
            a = self._off("sel.first")
            b = self._off("sel.last")
            if b > a:
                return self.preview_range(a, b)
        except tk.TclError:
            pass
        self.preview_from(self._off("insert"))

    def preview_from(self, off):
        text = self.get_text()
        if not text.strip():
            return
        if not self._segs_ready(lambda: self.preview_from(off), "La escucha"):
            return
        ls = text.rfind("\n", 0, off) + 1
        if off >= len(text.rstrip()):      # cursor al final (p. ej. justo después de pegar): desde el inicio
            ls = 0
        if self._plan_fresh():
            return self.play_plan(self._plan_index_at(ls))
        self.preview_range(ls, len(text))

    def preview_all(self):
        self.preview_range(0, len(self.get_text()))

    def preview_range(self, a, b):
        if not self._segs_ready(lambda: self.preview_range(a, b), "La escucha"):
            return
        st = self._settings()
        chunks = audio.build_chunks(self.segs, st, a, b)
        if chunks:
            self._stream(chunks, st)

    def _stream(self, chunks, st, label="Escuchando", from_plan=False):
        if not AUDIO_OK:
            messagebox.showerror(APP, "No se pudo iniciar el audio del sistema.")
            return
        if self.busy:       # no se corta una exportación a medias por pulsar ▶
            self.status("Hay un trabajo en curso (exportación o generación). Espera a que termine o pulsa Cancelar.")
            return
        self.stop_all()
        groups, i, size = [], 0, 6
        while i < len(chunks):
            groups.append(chunks[i:i + size])
            i += size
            size = min(40, size * 2)
        cancel = threading.Event()
        self._stream_cancel = cancel
        play = {"parts": [], "idx": -1, "offset": 0.0, "part_t0": 0.0, "paused_at": 0.0,
                "done": False, "waiting": True, "total_chunks": len(chunks), "cur": -1, "label": label,
                "cancel": cancel, "error": None, "from_plan": from_plan}
        self.play = play
        self.btn_play.configure(text="❚❚")
        self.btn_cancel.configure(state="normal")
        self.lbl_now.configure(text="Generando las primeras voces…", text_color=MUTED)
        workers = int(self.v_workers.get())
        bed = None
        if st.music_path and os.path.exists(st.music_path):
            try:
                bed = audio.MusicBed(st.music_path, st.out_rate, st.music_db, st.duck)
            except Exception:
                bed = None
        stamp = int(time.time() * 1000)

        def work():
            done_chunks = 0
            try:
                for gi, g in enumerate(groups):
                    # no adelantarse más de 10 minutos a lo que suena: con 500 mil palabras no se llena el disco
                    while not cancel.is_set() and sum(r.duration for _, _, r in play["parts"][play["idx"] + 1:]) > 600:
                        cancel.wait(1.0)
                    if cancel.is_set():
                        return
                    path = os.path.join(TMP, f"stream_{stamp}_{gi}.wav")
                    res = audio.render(g, path, st, "wav", cancel=cancel, workers=workers,
                                       bed=bed if bed else False, lead=(gi == 0), tail=False)
                    if cancel.is_set():
                        _drop_stream_file(path)
                        return
                    done_chunks += len(g)
                    play["parts"].append((path, g, res))
                    self.after(0, lambda g=g, res=res: self._mark_heard(g, res))
                    self.after(0, lambda d=done_chunks: self._stream_progress(play, d))
                play["done"] = True
            except audio.Cancelled:
                pass
            except Exception as e:
                play["error"] = e
                play["done"] = True
                self.after(0, lambda e=e: self._job_error(e))
        threading.Thread(target=work, daemon=True).start()

    def _stream_progress(self, play, d):
        if self.play is not play:
            return
        t = play["total_chunks"]
        self.pb.set(d / max(t, 1))
        self.status(f"{play['label']} · preparado {d}/{t}" + (" · completo" if d >= t else ""))

    def _play_file(self, path, chunks, res):
        """Reproduce un archivo ya hecho (por ejemplo el MP3 final)."""
        self.stop_all()
        play = {"parts": [(path, chunks, res)], "idx": -1, "offset": 0.0, "part_t0": 0.0,
                "paused_at": 0.0, "done": True, "waiting": True, "total_chunks": len(chunks), "cur": -1,
                "label": "Reproduciendo", "cancel": threading.Event(), "error": None}
        self.play = play
        self.btn_play.configure(text="❚❚")

    def _advance(self, p):
        nxt = p["idx"] + 1
        if nxt >= len(p["parts"]):
            return False
        if p["idx"] >= 0:
            p["offset"] += p["parts"][p["idx"]][2].duration
        p["idx"] = nxt
        path = p["parts"][nxt][0]
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        except Exception as e:
            self._job_error(e)
            self.stop_all()
            return False
        if nxt >= 1:
            _drop_stream_file(p["parts"][nxt - 1][0])
        p["part_t0"] = time.time()
        p["waiting"] = False
        p["starts"] = [t[0] for t in p["parts"][nxt][2].chunk_times]
        p["cur"] = -1
        return True

    def stop_all(self):
        p = self.play
        if p:
            p["cancel"].set()
        if AUDIO_OK:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        if p:
            for part in p["parts"]:
                _drop_stream_file(part[0])
        self.play = None
        self.paused = False
        self.btn_play.configure(text="▶")
        self.txt.tag_remove("reading", "1.0", "end")
        self.lbl_time.configure(text="")
        if not self.busy:
            self.btn_cancel.configure(state="disabled")

    def _tick(self):
        try:
            self._tick_inner()
            self._tick_n = getattr(self, "_tick_n", 0) + 1
            self._sleep_check()
            if self.play and self._tick_n % 300 == 0:          # ~15 s: si se cierra de golpe, no se pierde dónde ibas
                if getattr(self, "_saved_pos", None) != getattr(self, "_resume_pos", None):
                    self._saved_pos = self._resume_pos
                    self._autosave()
            if self._tick_n % 30 == 0 and self.segs and not self.busy and not self._plan_job:
                if not self._plan_fresh():
                    self.schedule_plan(200)
        except Exception as e:
            self._log_error(e)
        # 20 cuadros por segundo al sonar; en reposo 8 (menos CPU y la ventana se redimensiona fluida)
        self.after(50 if (self.play or self.busy) else 125, self._tick)

    def _tick_inner(self):
        w = max(self.viz.winfo_width(), 10)
        h = max(self.viz.winfo_height(), 30)
        p = self.play
        if p and AUDIO_OK and not self.paused:
            busy = pygame.mixer.music.get_busy()
            ended = (not busy and not p["waiting"] and time.time() - p["part_t0"] > 0.3)
            if p["idx"] < 0 or p["waiting"] or ended:
                if not self._advance(p):
                    if p["done"]:
                        self.stop_all()
                        self.lbl_now.configure(text="Fin de la reproducción", text_color=MUTED)
                        self.status("Escuchado completo. Si te gustó, «Generar» lo guarda al instante.")
                        return
                    p["waiting"] = True
                    self.lbl_now.configure(text="⏳ Generando la siguiente parte…", text_color=AMBER)
        if p and AUDIO_OK and p["idx"] >= 0 and not p["waiting"]:
            now = p["paused_at"] if self.paused else time.time()
            local = now - p["part_t0"]
            path, chunks, res = p["parts"][p["idx"]]
            pos = p["offset"] + local
            k = int(local / 0.05)
            nb = audio.SPEC_BANDS
            spec = getattr(res, "spectrum", b"")
            if spec and (k + 1) * nb <= len(spec):
                raw = [b / 255 for b in spec[k * nb:(k + 1) * nb]]
            else:
                env = res.envelope
                v = env[k] if 0 <= k < len(env) else 0
                raw = [min(1, v * 3.5) * (0.55 + 0.45 * math.sin(i * 0.7 + local * 9)) for i in range(nb)]
            if self.paused:
                raw = [0.0] * nb
            self._draw_spectrum(raw, w, h)
            ready = sum(r.duration for _, _, r in p["parts"])
            self.lbl_time.configure(text=f"{int(pos // 60):02d}:{int(pos % 60):02d} / "
                                         f"{int(ready // 60):02d}:{int(ready % 60):02d}" + ("" if p["done"] else "+"))
            i = bisect.bisect_right(p["starts"], local) - 1
            if p.get("from_plan") and 0 <= i < len(chunks):
                gi = getattr(chunks[i], "_i", None)
                if gi is not None and gi < len(self.plan_chunks) and self.plan_chunks[gi] is chunks[i]:
                    tp = self.plan_t0[gi] + min(self._est(chunks[i]), local - p["starts"][i])
                    if getattr(self, "_tl_target", None) is None:     # no mover el punto mientras lo arrastras
                        self._draw_timeline(tp)
                    tt = int(self.plan_total)
                    self.lbl_time.configure(text=f"{int(tp) // 3600}:{int(tp) % 3600 // 60:02d}:{int(tp) % 60:02d} / "
                                                 f"{tt // 3600}:{tt % 3600 // 60:02d}:{tt % 60:02d}" + self._sleep_left())
                elif gi is None and p.get("seek_base") is not None and getattr(self, "_tl_target", None) is None:
                    self._draw_timeline(p["seek_base"] + pos)    # frase cortada al saltar: aún no es un bloque del plan
            if 0 <= i < len(chunks) and i != p["cur"]:
                p["cur"] = i
                ch = chunks[i]
                if ch.src_end > ch.src_start:
                    self._resume_pos = ch.src_start          # «Continuar donde lo dejé» (F7)
                    self._cur_span = (ch.src_start, ch.src_end)  # lo que suena ahora (para 🔖 Marcar)
                self.txt.tag_remove("reading", "1.0", "end")
                if ch.src_end > ch.src_start:
                    try:
                        a, b = self._idx(ch.src_start), self._idx(ch.src_end)
                        self.txt.tag_add("reading", a, b)
                        self.txt.see(a)
                    except Exception:
                        pass
                who = ch.who or "Narrador"
                col = self.char_color.get(ch.who, AMBER if who == "Narrador" else TXT)
                self._spec_col = col if ch.who else CYAN
                vshort = ch.voice.split("-", 2)[-1].replace("Neural", "")
                if self._vmode() == "offline" or (self._vmode() == "auto" and not self.voices_online
                                                  and audio.offline_available()):
                    vshort = "voz natural"
                emo = brain.EMO_LABEL.get(ch.emo, "") if ch.emo and ch.emo not in ("neutral", "calma") else ""
                self.lbl_now.configure(text=f"🎙 {who}  ·  {vshort}" + (f"  ·  {emo}" if emo else ""),
                                       text_color=col)
            return
        self._viz_phase += 0.12 if (self.busy or p) else 0.03
        nb = audio.SPEC_BANDS
        amp = 0.32 if (self.busy or p) else 0.1
        raw = [amp * (0.5 + 0.5 * math.sin(i * 0.42 + self._viz_phase * 2.2)) *
               (0.6 + 0.4 * math.sin(i * 0.13 - self._viz_phase)) for i in range(nb)]
        self._spec_col = VIOLET if (self.busy or p) else CYAN_D
        self._draw_spectrum(raw, w, h, idle=True)

    def _draw_spectrum(self, raw, w, h, idle=False):
        """Espectro neón espejado: barras con halo, picos que caen y reflejo bajo la línea."""
        if time.time() < self._resize_until:
            return          # mientras cambias el tamaño de la ventana no se redibuja
        c = self.viz
        c.delete("all")
        nb = len(raw)
        s, pk = self._spec_s, self._spec_peak
        for i, v in enumerate(raw):
            s[i] = v if v > s[i] else s[i] * 0.72 + v * 0.28
            pk[i] = max(s[i], pk[i] - 0.025)
        base = h * 0.68
        top = base - 4
        slot = max(4.0, (w - 8) / (nb * 2))
        bw = max(2, int(slot * 0.55))
        cx = w / 2
        accent = self._spec_col
        hot = "#f0abfc"
        glow_bg = SURF
        c.create_line(8, base, w - 8, base, fill=mix(glow_bg, accent, 0.35), width=1)
        for side in (-1, 1):
            for i in range(nb):
                v = s[i]
                x = cx + side * (i + 0.5) * slot
                bh = max(1.5, v * top)
                col = mix(accent, hot, min(1, i / nb * 1.2 + v * 0.3)) if not idle else mix(accent, VIOLET, i / nb)
                c.create_line(x, base, x, base - bh, fill=mix(glow_bg, col, 0.22), width=bw + 6, capstyle="round")
                c.create_line(x, base, x, base - bh, fill=mix(glow_bg, col, 0.55), width=bw + 2, capstyle="round")
                c.create_line(x, base, x, base - bh * 0.6, fill=col, width=bw, capstyle="round")
                c.create_line(x, base - bh * 0.6, x, base - bh, fill=mix(col, "#ffffff", 0.45), width=bw,
                              capstyle="round")
                c.create_line(x, base + 2, x, base + 2 + bh * 0.32, fill=mix(glow_bg, col, 0.28), width=bw)
                if not idle and pk[i] > 0.12:
                    y = base - pk[i] * top - 2
                    c.create_line(x - bw / 2, y, x + bw / 2, y, fill=mix(col, "#ffffff", 0.6), width=2)

    HELP = [
        ("HISTORIA", [("Escribir o pegar", "Escribe o pega en el editor y pulsa ▶. Los diálogos con raya (—), comillas o guion se detectan solos."),
                      ("Abrir / arrastrar", docs.FORMATS + ". Se quitan negritas, números de página y cortes de línea."),
                      ("Clic derecho en un diálogo", "Eliges quién lo dice y cómo lo dice (susurro, grito…)."),
                      ("Re-analizar", "Vuelve a leer el texto si lo editaste mucho."),
                      ("☰ Más", "Continuar donde lo dejé, leer lo copiado, reemplazar todo por lo copiado o añadirlo al final, "
                                "marcadores, preguntas de repaso, buscar y reemplazar, convertir varios documentos.")]),
        ("ESTUDIO", [("∑ Fórmulas", "Botón ∑ de la barra (Automático, Siempre, Nunca). Automático: solo las frases con fórmulas se leen "
                                   "como matemática (d²y/dx², ω, 220 V, ∫, ẋ, |x|, n!, ∠30°); el «·» de «Lima · Perú» es una pausa."),
                     ("Cómo se leerá", "Clic derecho › «Cómo se leerá esto»: muestra la frase exacta que dirá la voz."),
                     ("Citas y bibliografía", "Salta [12] y (Autor, 2020) y deja de leer en «Referencias» (pestaña IA)."),
                     ("PDF escaneados y fotos", "Se reconoce el texto con el OCR de Windows, sin internet."),
                     ("Marcadores y preguntas", "Ctrl+B marca lo que suena, F9 abre tus notas (apuntes .md o tarjetas Anki). "
                                                "☰ Más › Preguntas de repaso: tu IA pregunta con la cita del texto.")]),
        ("REPARTO", [("Narrador", "Voz que lee todo lo que no es diálogo."),
                     ("Tarjeta de personaje", "Voz, tono, edad y ▶ para probar. En ámbar: Kuon IA duda si es mujer u hombre."),
                     ("⇄", "Une dos nombres del mismo personaje (Mefisto = Mefistófeles, el rey = Hades)."),
                     ("✕", "Quita algo que no es un personaje. Kuon IA lo recuerda."),
                     ("Círculo de color", "Clic para elegir el color del personaje (en el texto, la tarjeta y la línea de tiempo)."),
                     ("↶ / ↷", "Deshacer y rehacer cualquier cambio del reparto (Ctrl+Z / Ctrl+Y fuera del editor).")]),
        ("VOZ", [("Multivoz / Solo el narrador", "Cada personaje con su voz, o toda la historia con una sola."),
                 ("Idioma de la narración", "Acento de las voces. Al cambiarlo se reparten las mejores de ese acento."),
                 ("Estilo e intensidad", "Velocidad, tono y cuánto se notan las emociones (0 % plano, 100 % natural).")]),
        ("IA", [("Seguridad mínima", "Si Kuon IA no está así de segura de quién habla, lee el narrador."),
                ("Memoria", "Lo que aprendió de tus correcciones. «Olvidar todo» la reinicia."),
                ("Ollama", "Opcional: revisa los diálogos dudosos con un modelo de IA instalado en tu PC.")]),
        ("ASISTENTE", [("Configurar", "Elige el servicio (Gemini, OpenRouter, Groq, OpenAI, DeepSeek, Ollama u otro), pega tu clave y "
                                      "elige el modelo. La clave se guarda cifrada con tu cuenta de Windows."),
                       ("Hablar", "Pregunta lo que quieras de tu historia. Usa los personajes y la línea donde está el cursor."),
                       ("Revisar dudosos", "Lee los diálogos en los que Kuon IA duda y propone quién los dice."),
                       ("Aplicar", "Los cambios propuestos solo se aplican si los aceptas; Kuon IA aprende de ellos y se deshacen con ↶.")]),
        ("TECLADO", KEYS),
        ("DICCIÓN", [("Palabras dudosas", "Nombres en inglés o inventados: «Como se escribe», «En inglés» o «Sin tocar»."),
                     ("Diccionario propio", "Una línea por palabra: Palabra = cómo suena.")]),
        ("REPRODUCTOR", [("▶ / ■ / Todo", "Escucha desde el cursor, detiene, o empieza desde el inicio."),
                         ("Línea de tiempo", "Clic para saltar. Brillante: ya generado. Ámbar: cambió desde la última vez."),
                         ("Cambios", "Salta a cada parte cambiada para revisarla antes de guardar."),
                         ("Generar audio final", "MP3 o WAV con subtítulos y capítulos para YouTube. Solo genera lo que falta.")]),
    ]
    TOUR = [
        ("Escribe, pega o abre tu historia", "Arrastra un documento a la ventana o escribe en el editor. "
         "Formatos: " + docs.FORMATS + "."),
        ("Reparto", "Kuon IA reconoce a los personajes, su género y su edad, y les da voz. Clic derecho en un diálogo "
         "para corregir quién habla o con qué emoción; lo recuerda para la próxima vez."),
        ("Voz", "Idioma y acento (México, Colombia, Perú, Argentina, España y más), estilo, velocidad y cuánto se notan las emociones."),
        ("Kuon IA", "Qué hace la IA: voces por personaje, emociones, ritmo de escena y seguridad mínima. "
         "Usa 4 diccionarios: nombres con género, palabras comunes, verbos de habla y pronunciación inglesa."),
        ("Música", "Música de fondo que baja sola cuando alguien habla."),
        ("Exportar", "MP3 o WAV, subtítulos .srt y capítulos listos para la descripción de YouTube."),
        ("Dicción", "Nombres en inglés o inventados (Nightwolf, Lovegod): elige cómo se leen, y tu diccionario propio «Palabra = cómo suena»."),
        ("Escucha antes de guardar", "▶ suena desde el cursor. La línea de tiempo te deja saltar a cualquier parte; "
         "lo que cambies se marca en ámbar y «Cambios» te lleva a cada parte nueva."),
        ("Asistente", "Habla con una IA sobre tu historia. Pon tu clave de Gemini, OpenRouter u otro servicio (o usa Ollama sin "
         "internet) y pídele que revise los diálogos dudosos. Tú aceptas o descartas lo que propone."),
        ("☰ Más y ∑ Fórmulas", "En ☰ Más: continuar donde lo dejaste, reemplazar todo por lo copiado o añadirlo al final, "
         "🔖 marcadores (Ctrl+B) y notas (F9), 🧠 preguntas de repaso. El botón ∑ decide cómo se leen las fórmulas: "
         "Automático (solo las frases con fórmulas), Siempre o Nunca."),
        ("🔍 Revisar dudosos", "En Reparto: los diálogos en que Kuon IA dudó, uno por uno. Eliges quién lo dice (teclas 1-9) y aprende. "
         "Con tu IA puede sugerirlo; al pegar la clave los modelos aparecen solos."),
        ("Generar audio final", "Cuando te guste, se guarda en MP3 con subtítulos. Solo genera lo que falta. Los cambios se "
         "deshacen con ↶ y la ayuda está en el botón ? de arriba."),
    ]

    def _overlay(self, title, build, width=560, height=560):
        w = ctk.CTkToplevel(self, fg_color=SURF)
        w.title(title)
        w.geometry(f"{width}x{height}+{self.winfo_rootx() + 120}+{self.winfo_rooty() + 80}")
        w.transient(self)
        w.after(80, w.lift)
        build(w)
        return w

    def show_help(self):
        def build(w):
            sc = ctk.CTkScrollableFrame(w, fg_color=SURF)
            sc.pack(fill="both", expand=True, padx=10, pady=10)
            m = img("mascota_saludo", 96)
            if m:
                ctk.CTkLabel(sc, text="", image=m).pack(anchor="e")
            for sec, items in self.HELP:
                ctk.CTkLabel(sc, text=sec, font=font(12, "bold"), text_color=CYAN).pack(anchor="w", pady=(10, 2))
                for a, b in items:
                    ctk.CTkLabel(sc, text=f"{a}  ·  {b}", font=font(12), text_color=TXT, wraplength=490,
                                 justify="left").pack(anchor="w", pady=1)
            self._btn(sc, "Ver el tutorial", lambda: (w.destroy(), self.start_tour()), width=160).pack(pady=12)
        self._overlay("Cómo se usa", build)

    def start_tour(self, i=0):
        if getattr(self, "_tour_win", None):
            try:
                self._tour_win.destroy()
            except Exception:
                pass
        if i >= len(self.TOUR):
            self.cfg["tour_done"] = True
            return
        tab = {1: "Reparto", 2: "Voz", 3: "IA", 4: "Música", 5: "Exportar", 6: "Dicción", 8: "Asistente"}.get(i)
        if tab:
            self.tabs.set(tab)
        t, x = self.TOUR[i]

        def build(w):
            m = img(("mascota_saludo", "mascota_pensando", "mascota_escuchando", "mascota_base")[i % 4], 110)
            if m:
                ctk.CTkLabel(w, text="", image=m).pack(side="left", anchor="n", padx=(12, 0), pady=14)
            ctk.CTkLabel(w, text=f"{i + 1} de {len(self.TOUR)}", font=font(11), text_color=MUTED).pack(anchor="w", padx=16, pady=(14, 0))
            ctk.CTkLabel(w, text=t, font=font(17, "bold", "Bahnschrift"), text_color=CYAN).pack(anchor="w", padx=16, pady=(2, 6))
            ctk.CTkLabel(w, text=x, font=font(13), text_color=TXT, wraplength=380 if m else 400, justify="left").pack(anchor="w", padx=16)
            row = ctk.CTkFrame(w, fg_color="transparent")
            row.pack(side="bottom", fill="x", padx=16, pady=14)
            self._btn(row, "Empezar" if i == len(self.TOUR) - 1 else "Siguiente", lambda: self.start_tour(i + 1),
                      accent=True, width=120).pack(side="right")
            self._btn(row, "Saltar", lambda: self.start_tour(len(self.TOUR)), width=80).pack(side="right", padx=6)
        self._tour_win = self._overlay("Tutorial", build, 560, 250)

    def _news_then_open(self):
        self.show_news()
        self._news_pending = False
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.load_file(sys.argv[1])

    def show_news(self):
        notes = "\n\n".join("• " + n for n in APP_VERSION.get("notes", []))
        messagebox.showinfo(APP, f"Novedades de la {APP_VERSION['label']}\n\n{notes}")

    def show_log(self):
        p = os.path.join(CFG_DIR, "error.log")
        if os.path.exists(p):
            try:
                os.startfile(p)
            except Exception:
                messagebox.showinfo(APP, open(p, encoding="utf-8", errors="replace").read()[-3000:])
        else:
            messagebox.showinfo(APP, "Sin errores registrados.")

    # ------------------------------------------------------------ plan completo y línea de tiempo
    def _plan_sig(self):
        st = self._settings()
        d = dict(st.__dict__)
        d.pop("music_path", None), d.pop("music_db", None), d.pop("duck", None), d.pop("bitrate", None)
        return hash((json.dumps(d, sort_keys=True, ensure_ascii=False, default=str), self.analysis_gen))

    def _plan_fresh(self):
        return bool(self.plan_chunks) and getattr(self, "_plan_sig_v", None) == self._plan_sig()

    def schedule_plan(self, delay=500):
        if self._plan_job:
            self.after_cancel(self._plan_job)
        self._plan_job = self.after(delay, self._build_plan)

    def _est(self, c):
        return len(c.text) / (self.cfg.get("cps", 14.5) * max(0.3, 1 + c.rate / 100)) + c.pause_ms / 1000

    def _build_plan(self):
        self._plan_job = None
        if not self.segs:
            return
        text, segs, chapters, st = self.get_text(), list(self.segs), list(self.chapters), self._settings()
        sig = self._plan_sig()
        self._plan_gen += 1
        gen = self._plan_gen
        heard = set(self.heard)

        def work():
            try:
                chunks = audio.plan(text, segs, chapters, st)
                t0 = [0.0]
                stat = []
                for i, c in enumerate(chunks):
                    c._i = i
                    t0.append(t0[-1] + self._est(c))
                    cp = audio._cache_path(c, st.voice_mode == "offline")
                    ok = bool(cp and os.path.exists(cp))
                    stat.append(1 if ok else (2 if c.src_start in heard else 0))
                self.after(0, lambda: self._plan_ready(gen, sig, chunks, t0, stat))
            except Exception as e:
                self._log_error(e)
        threading.Thread(target=work, daemon=True).start()

    def _plan_ready(self, gen, sig, chunks, t0, stat):
        if gen != self._plan_gen:
            return
        self.plan_chunks, self.plan_t0, self.plan_stat = chunks, t0, stat
        self.plan_total = t0[-1]
        self._plan_sig_v = sig
        self._draw_timeline()

    def _plan_index_at(self, off):
        lo, hi, r = 0, len(self.plan_chunks) - 1, 0
        while lo <= hi:
            m = (lo + hi) // 2
            if self.plan_chunks[m].src_start <= off:
                r, lo = m, m + 1
            else:
                hi = m - 1
        return r

    def _plan_index_at_time(self, t):
        return max(0, min(len(self.plan_chunks) - 1, bisect.bisect_right(self.plan_t0, t) - 1))

    def play_plan(self, i, label="Escuchando"):
        if not self.plan_chunks:
            return
        i = max(0, min(i, len(self.plan_chunks) - 1))
        self._stream(self.plan_chunks[i:], self._settings(), label, from_plan=True)

    def _mark_heard(self, chunks, res):
        if audio.SKIPPED:
            sk = list(audio.SKIPPED)
            audio.SKIPPED.clear()
            self._log_error(RuntimeError("fragmentos saltados (la voz no los pudo leer): " + " | ".join(sk)))
            self.status(f"{len(sk)} fragmento(s) no se pudieron leer y se saltaron; la narración sigue.")
        changed = False
        for c, (a, b) in zip(chunks, res.chunk_times):
            self.heard.add(c.src_start)
            i = getattr(c, "_i", None)
            if i is not None and i < len(self.plan_chunks) and self.plan_chunks[i] is c and self.plan_stat[i] != 1:
                self.plan_stat[i] = 1
                changed = True
            dur = b - a
            if dur > 0.4 and len(c.text) > 20:
                obs = len(c.text) / (dur * max(0.3, 1 + c.rate / 100))
                self.cfg["cps"] = max(8.0, min(30.0, self.cfg.get("cps", 14.5) * 0.97 + obs * 0.03))
        if changed:
            self._draw_timeline()

    def play_changes(self):
        if not self.plan_stat:
            return
        cur = 0
        p = self.play
        if p and p.get("from_plan") and p["idx"] >= 0 and p["cur"] >= 0:
            c = p["parts"][p["idx"]][1][p["cur"]]
            cur = getattr(c, "_i", 0) + 1
        n = len(self.plan_stat)
        for k in range(n):
            j = (cur + k) % n
            if self.plan_stat[j] == 2:
                prev = j - 1 if j > 0 and self.plan_chunks[j - 1].chapter == self.plan_chunks[j].chapter else j
                return self.play_plan(prev, "Revisando cambios")

    def _tl_later(self):
        if getattr(self, "_tl_job", None):
            self.after_cancel(self._tl_job)
        self._tl_job = self.after(180, lambda: (setattr(self, "_tl_job", None), self._draw_timeline()))

    TL_PAD, TL_TH = 10, 6

    def _draw_timeline(self, pos=None):
        """Pista fina y redondeada: gris = sin generar, azul tenue = ya generado, cian = lo escuchado,
        punto brillante = dónde vas (con el color de quien habla), cortes = capítulos, ámbar = cambios."""
        c = self.tl
        w, h = max(c.winfo_width(), 10), max(c.winfo_height(), 16)
        pad, th, cy = self.TL_PAD, self.TL_TH, h / 2
        x0, x1 = pad, w - pad
        tw = max(1, x1 - x0)
        if pos is None:
            c.delete("all")
            n = len(self.plan_chunks)
            c.create_line(x0, cy, x1, cy, fill="#1a2440", width=th, capstyle="round")
            if n:
                T = self.plan_total or 1
                step, run, chg_runs, cstart = 2, None, [], None
                for x in range(0, int(tw) + step, step):
                    i0 = self._plan_index_at_time(min(1.0, x / tw) * T)
                    i1 = max(i0, self._plan_index_at_time(min(1.0, (x + step) / tw) * T))
                    seg = self.plan_stat[i0:i1 + 1]
                    ready = bool(seg) and all(v == 1 for v in seg)
                    if ready and run is None:
                        run = x
                    if (not ready or x + step > tw) and run is not None:
                        c.create_line(x0 + run, cy, x0 + min(x, tw), cy, fill="#33507e", width=th, capstyle="round")
                        run = None
                    chg = any(v == 2 for v in seg)
                    if chg and cstart is None:
                        cstart = x
                    if (not chg or x + step > tw) and cstart is not None:
                        chg_runs.append((cstart, x))
                        cstart = None
                last = -1
                for i, ch in enumerate(self.plan_chunks):          # capítulos: un corte en la pista
                    if ch.chapter != last:
                        last = ch.chapter
                        if i:
                            x = x0 + self.plan_t0[i] / T * tw
                            c.create_line(x, cy - th, x, cy + th, fill=SURF, width=2)
                for a, b in chg_runs:                               # cambios: puntos ámbar encima de la pista
                    c.create_line(x0 + a, cy - th - 2, x0 + max(b, a + 3), cy - th - 2, fill=AMBER, width=3, capstyle="round")
                nchg = sum(1 for v in self.plan_stat if v == 2)
                ready = sum(1 for v in self.plan_stat if v == 1)
                t = int(T)
                pct = 100 * ready // n
                self.lbl_tl.configure(text=f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
                                           + (f" · {pct} % listo" if pct < 100 else " · listo")
                                           + (f" · {nchg} cambios" if nchg else ""))
                if nchg:
                    self.btn_chg.configure(text=f"Cambios {nchg}")
                    self.btn_chg.pack(pady=(4, 0))
                else:
                    self.btn_chg.pack_forget()
                rp = int(getattr(self, "_resume_pos", 0) or 0)      # en reposo: dónde quedaste
                self._tl_head(self.plan_t0[self._plan_index_at(rp)] if 0 < rp and self.plan_t0 else 0, idle=True)
            return
        if self.plan_total:
            self._tl_head(pos)

    def _tl_head(self, pos, idle=False):
        c = self.tl
        c.delete("ph")
        w, h = max(c.winfo_width(), 10), max(c.winfo_height(), 16)
        pad, th, cy = self.TL_PAD, self.TL_TH, h / 2
        tw = max(1, w - 2 * pad)
        x = pad + max(0.0, min(1.0, pos / (self.plan_total or 1))) * tw
        col = VIOLET if idle else (getattr(self, "_spec_col", CYAN) or CYAN)
        if x > pad + 1:
            c.create_line(pad, cy, x, cy, fill=mix(CYAN, VIOLET, 0.35) if not idle else mix(SURF, VIOLET, 0.55),
                          width=th, capstyle="round", tags="ph")
        r = 8 if getattr(self, "_tl_target", None) is not None else 6
        c.create_oval(x - r - 3, cy - r - 3, x + r + 3, cy + r + 3, fill=mix(SURF, col, 0.35), outline="", tags="ph")
        c.create_oval(x - r, cy - r, x + r, cy + r, fill="#ffffff", outline="", tags="ph")
        c.create_oval(x - r * 0.45, cy - r * 0.45, x + r * 0.45, cy + r * 0.45, fill=col, outline="", tags="ph")

    # --- saltar con precisión: del tiempo tocado a la frase más cercana (no al inicio del bloque)
    _SENT_RE = re.compile(r"[.!?…][\"»”’')\]]*\s+|\n+")

    def _sentence_cut(self, off):
        segs = self.segs
        lo, hi, k = 0, len(segs) - 1, -1
        while lo <= hi:
            m = (lo + hi) // 2
            if segs[m].start <= off:
                k, lo = m, m + 1
            else:
                hi = m - 1
        if k < 0:
            return None
        s = segs[k]
        rel = max(0, min(len(s.text), off - s.start))
        cut = 0
        if len(s.text) >= 90 and rel >= 12:
            for m in self._SENT_RE.finditer(s.text):
                p = m.end()
                if p >= len(s.text) - 3 or p > rel + 12:
                    break
                cut = p
        return k, s, cut

    def _time_to_off(self, t):
        i = self._plan_index_at_time(t)
        c = self.plan_chunks[i]
        f = max(0.0, min(1.0, (t - self.plan_t0[i]) / max(0.1, self._est(c))))
        return int(round(c.src_start + f * (c.src_end - c.src_start)))

    def _seek_time(self, t):
        if not self.plan_chunks:
            return
        off = self._time_to_off(t)
        i = self._plan_index_at(off)
        c = self.plan_chunks[i]
        nxt = self.plan_chunks[i + 1] if i + 1 < len(self.plan_chunks) else None
        r = self._sentence_cut(off)
        if not r or r[1].start + r[2] <= c.src_start + 2:
            return self.play_plan(i)
        k, s, cut = r
        start = s.start + cut
        st = self._settings()
        segs = [dataclasses.replace(s, start=start, text=s.text[cut:])] + list(self.segs[k + 1:])
        first = audio.build_chunks(segs, st, start, nxt.src_start if nxt else None, c.chapter)
        if not first:
            return self.play_plan(i)
        self._stream(first + self.plan_chunks[i + 1:], st, "Escuchando", from_plan=True)
        if self.play:
            frac = (start - c.src_start) / max(1, c.src_end - c.src_start)
            self.play["seek_base"] = self.plan_t0[i] + frac * self._est(c)

    def _tl_hover(self, e):
        if not self.plan_chunks:
            return
        w = max(self.tl.winfo_width(), 10)
        f = (e.x - self.TL_PAD) / max(1, w - 2 * self.TL_PAD)
        t = max(0.0, min(1.0, f)) * self.plan_total
        self._tl_target = t
        off = self._time_to_off(t)
        r = self._sentence_cut(off)
        at = r[1].start + r[2] if r else off
        cap = self.plan_chunks[self._plan_index_at(at)].chapter + 1
        snip = " ".join(self.get_text()[at:at + 70].split())
        ti = int(t)
        self.lbl_tl.configure(text=f"▶ {ti // 3600}:{ti % 3600 // 60:02d}:{ti % 60:02d} · capítulo {cap}")
        self.lbl_now.configure(text=f"▶ «{snip}…»", text_color=MUTED)
        self._tl_head(t)

    def _tl_release(self, _e):
        t = getattr(self, "_tl_target", None)
        self._tl_target = None
        if t is not None:
            self._seek_time(t)

    def _test(self, chunk):
        self._stream([chunk], self._settings(), "Probando voz")

    def _sample_line(self, nm):
        for s in self.segs:
            if s.kind == "dlg" and s.speaker == nm and 20 < len(s.text) < 220:
                return docs.clean_for_speech(s.text)
        return f"Soy {nm}. Esta es la voz que tendré en la historia."

    def test_char(self, nm):
        c = self.cast[nm]
        st = self._settings()
        rate = round((st.speed - 1) * 100) + int(c.get("rate", 0))
        ch = audio.Chunk(self._sample_line(nm), c["voice"], rate, int(c.get("pitch", 0)), 1.0, 200, 0, 0, nm)
        self._test(ch)

    def test_narrator(self):
        st = self._settings()
        text = next((s.text for s in self.segs if s.kind == "narr" and 60 < len(s.text) < 400),
                    "La noche cayó sobre la ciudad, y con ella llegó el silencio.")
        self._test(audio.Chunk(docs.clean_for_speech(text), st.narrator, round((st.speed - 1) * 100),
                               st.pitch, 1 + st.volume / 100, 200, 0, 0, ""))

    def test_lexicon(self):
        try:
            sel = self.txt.get("sel.first", "sel.last")
        except tk.TclError:
            sel = ""
        st = self._settings()
        text = docs.apply_lexicon(docs.apply_pron(sel or "Ryūjin Jakka", docs.pron_rules(st.pron)),
                                  docs.parse_lexicon(st.lexicon))
        self._test(audio.Chunk(text, st.narrator, round((st.speed - 1) * 100), st.pitch, 1.0, 200, 0, 0))

    # ------------------------------------------------------------ exportar
    def pick_music(self):
        p = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg")])
        if p:
            self.v_music.set(p)

    def pick_outdir(self):
        d = filedialog.askdirectory(initialdir=self.v_outdir.get())
        if d:
            self.v_outdir.set(d)

    def open_outdir(self):
        d = self.v_outdir.get()
        if os.path.isdir(d) and hasattr(os, "startfile"):
            os.startfile(d)

    @staticmethod
    def _safe_name(s):
        s = "".join(c for c in s if c not in '\\/:*?"<>|\n\r\t').strip()
        return s[:90] or "audio"

    def generate(self):
        if self.busy:
            return
        text = self.get_text()
        if not text.strip():
            messagebox.showinfo(APP, "No hay texto. Arrastra un documento o pégalo en el editor.")
            return
        if not self._segs_ready(self.generate, "La generación"):
            return
        outdir = self.v_outdir.get()
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP, f"Carpeta de destino no válida:\n{e}")
            return
        fmt = self.v_fmt.get().lower()
        base = self._safe_name(os.path.splitext(self.doc_key)[0] if self.doc_path else "narracion")
        split = self.v_split.get() and len(self.chapters) > 1
        target = os.path.join(outdir, f"{base}.{fmt}")
        if not split:
            target = filedialog.asksaveasfilename(initialdir=outdir, initialfile=f"{base}.{fmt}",
                                                  defaultextension=f".{fmt}",
                                                  filetypes=[(fmt.upper(), f"*.{fmt}")])
            if not target:
                return
        self._store()
        self.stop_all()
        st = self._settings()
        segs, chapters = list(self.segs), list(self.chapters)
        srt, yt = self.v_srt.get(), self.v_yt.get()
        mp4, cover = self.v_mp4.get(), self.v_cover.get().strip()
        vopt = dict(bg=self.v_vbg.get(), subs=self.v_vsubs.get(), names=self.v_vnames.get(), vertical=self.v_vvert.get(),
                    colors=dict(self.char_color), chapters=[c[0] for c in self.chapters],
                    ai_style=self.v_vstyle.get(), ai_engine=self.v_vqual.get())
        thumb_auto = bool(getattr(self, "v_thumbauto", None) and self.v_thumbauto.get())
        workers = int(self.v_workers.get())
        title = os.path.splitext(self.doc_key)[0] if self.doc_path else "Audionovela"
        vopt["doc_title"] = title
        self._start_job("Preparando…")

        def work():
            t0 = time.time()
            want_mp4 = mp4          # copia propia: si falta FFmpeg se apaga para los capítulos siguientes
            try:
                allc = audio.plan(text, segs, chapters, st)       # el plan se arma una vez y fuera de la ventana
                try:
                    cached = audio.cached_count(allc, audio.use_offline(st))
                    self.after(0, lambda: self.status(f"Preparando… {cached}/{len(allc)} fragmentos ya escuchados "
                                                      "(instantáneos)"))
                except Exception:
                    pass
                if split:
                    total, done, outs = len(allc), 0, []
                    for i, (ct, a, b) in enumerate(chapters):
                        cc = [c for c in allc if c.chapter == i]
                        if not cc:
                            continue
                        path = os.path.join(outdir, f"{i + 1:02d} - {self._safe_name(ct)}.{fmt}")
                        res = audio.render(cc, path, st, fmt, cancel=self.cancel, workers=workers,
                                           progress=lambda d, t, o=done: self._progress(o + d, total))
                        if srt:
                            audio.write_srt(res, os.path.splitext(path)[0] + ".srt")
                        if want_mp4:
                            want_mp4 = self._make_video(path, ct, cover, res, cc, vopt)
                        if thumb_auto:
                            self._make_thumb(path, title, [ct], cc, vopt)
                        done += len(cc)
                        outs.append((path, cc, res))
                    last = outs[-1] if outs else (None, None, None)
                    msg = f"{len(outs)} archivos en {outdir}"
                    self.after(0, lambda: (self.heard.update(c.src_start for c in allc),
                                           self._finished(last[0], last[1], last[2], msg, time.time() - t0)))
                else:
                    chunks = allc
                    res = audio.render(chunks, target, st, fmt, progress=self._progress, cancel=self.cancel,
                                       workers=workers)
                    if srt:
                        audio.write_srt(res, os.path.splitext(target)[0] + ".srt")
                    if yt:
                        audio.write_stamps(res, chapters, os.path.splitext(target)[0] + "_youtube.txt",
                                           title, st.cast)
                    if want_mp4:
                        self._make_video(target, title, cover, res, chunks, vopt)
                    if thumb_auto:
                        self._make_thumb(target, title, [c[0] for c in chapters], chunks, vopt)
                    self.after(0, lambda: self._finished(target, chunks, res, f"Guardado: {os.path.basename(target)}",
                                                         time.time() - t0))
            except audio.Cancelled:
                self.after(0, lambda: (self._end_job(), self.status("Generación cancelada")))
            except Exception as e:
                self.after(0, lambda e=e: self._job_error(e))
            finally:
                if self._ia_started:          # apaga el motor de imágenes que encendimos para los fondos
                    self._ia_started = False
                    imagen_ia.stop()
        threading.Thread(target=work, daemon=True).start()

    _ia_started = False

    def _ia_refresh(self):
        """Muestra las opciones de imágenes con IA si esta PC tiene el motor; si no, el botón para instalarlo."""
        try:
            engines = imagen_ia.available_engines()
            self._ia_bgmenu.configure(values=list(video.BACKGROUNDS) + ["Mi portada (zoom lento)"] +
                                      ([AI_BG] if engines else []))
            self._ia_engmenu.configure(values=[imagen_ia.AUTO] + engines)
            if self.v_vqual.get() not in engines:
                self.v_vqual.set(imagen_ia.AUTO)
            if engines:
                self._ia_setup.pack_forget()
                if not self._ia_arow.winfo_ismapped():
                    self._ia_arow.pack(fill="x", padx=4, pady=(0, 2), after=self._ia_brow)
            else:
                self._ia_arow.pack_forget()
                if not self._ia_setup.winfo_ismapped():
                    self._ia_setup.pack(fill="x", padx=4, pady=(0, 4), after=self._ia_brow)
                if self.v_vbg.get() == AI_BG:
                    self.v_vbg.set("Estrellas")
        except Exception as e:
            self._log_error(e)

    def open_ia_setup(self):
        import imagenes_app

        def done():
            self._ia_refresh()
            self.v_vbg.set(AI_BG)
            self._autosave()
            self.status("Imágenes con IA listas: elige el fondo «Imágenes con IA» o crea tu portada con «IA»")
        imagenes_app.open_setup(self, on_done=done)

    def open_image_studio(self):
        """Estudio de Imágenes dentro de la app: la imagen elegida queda como portada del video."""
        if getattr(self, "_img_win", None) is not None and self._img_win.winfo_exists():
            self._img_win.lift()
            return
        import imagenes_app

        def use(path):
            self.v_cover.set(path)
            if not self.v_vbg.get().startswith(("Mi portada", AI_BG)):
                self.v_vbg.set("Mi portada (zoom lento)")
            self._autosave()
            self.status("Portada lista: " + os.path.basename(path))
        self._img_win = imagenes_app.open_window(self, on_use=use, use_label="Usar como portada")

    @staticmethod
    def _ai_segments(res, chunks, target=240.0, max_n=24):
        """Tramos del audio que llevan su propia imagen: cada capítulo y, si es largo, cada ~4 min."""
        times = res.chunk_times if res is not None else []
        for tgt in (target, float("inf")):
            segs = []
            for i, c in enumerate(chunks or []):
                if i >= len(times):
                    break
                t0 = times[i][0]
                if not segs or c.chapter != segs[-1][2] or t0 - segs[-1][0] >= tgt:
                    segs.append([t0, [], c.chapter])
                if sum(len(x) for x in segs[-1][1]) < 3000:
                    segs[-1][1].append(c.text)
            if len(segs) <= max_n:
                return segs
        step = -(-len(segs) // max_n)
        return [segs[k] for k in range(0, len(segs), step)]

    def _ai_backgrounds(self, title, res, chunks, vopt, prog):
        """Crea (o reutiliza) una imagen por tramo. Devuelve [(t0, ruta)] o lanza RuntimeError."""
        import hashlib
        engines = imagen_ia.available_engines()
        if not engines:
            raise RuntimeError("no están ComfyUI o sus modelos en esta PC")
        eng = vopt.get("ai_engine")
        if eng not in engines:
            eng = imagen_ia.AUTO               # automático: la IA elige el motor según cada escena y el estilo
        style = vopt.get("ai_style", "Automático")
        w, h = imagen_ia.FORMATS["Vertical 9:16 (Shorts, TikTok)" if vopt.get("vertical")
                                 else "Horizontal 16:9 (video, YouTube)"]
        d = os.path.join(CFG_DIR, "fondos_ia")
        os.makedirs(d, exist_ok=True)
        out, todo = [], []
        story = vopt.get("doc_title") or title
        for t0, texts, _ch in self._ai_segments(res, chunks):
            txt = " ".join(texts)
            key = hashlib.sha1(f"poster2|{eng}|{style}|{w}x{h}|{story}|{txt[:3000]}".encode("utf-8")).hexdigest()[:20]
            p = os.path.join(d, key + ".png")
            out.append((t0, p))
            if not os.path.isfile(p):
                todo.append((p, txt))
        if not todo:
            return out
        model = imagen_ia.ensure_ollama()
        # prompts «estilo ChatGPT» (como tus ejemplos): los escribe tu API si está lista; si no, tu IA local
        write, who = self._prompt_writer()
        bible = {}
        if write:
            prog(f"{who}: ficha de los personajes de la obra", 0)
            bfile = os.path.join(d, "ficha_" + hashlib.sha1(story.encode("utf-8")).hexdigest()[:12] + ".json")
            try:
                with open(bfile, encoding="utf-8") as fh:
                    bible = json.load(fh)
            except Exception:
                try:
                    sample = " ".join(" ".join(t) for _t0, t, _c in self._ai_segments(res, chunks)[:4])
                    bible = imagen_ia.story_bible(sample, story, write)
                    with open(bfile, "w", encoding="utf-8") as fh:
                        json.dump(bible, fh, ensure_ascii=False, indent=1)
                except Exception as e:
                    self._log_error(e)
        z = "Alta calidad (Z-Image Turbo)"
        plans = []
        for k, (_p, txt) in enumerate(todo):
            if self.cancel.is_set():
                raise RuntimeError("CANCELLED")
            prog(f"{who or 'Tu IA'} escribe el prompt de la escena {k + 1} de {len(todo)}", k / len(todo))
            pp = ""
            if write:
                try:
                    pp = imagen_ia.poster_prompt(txt, story, bible, write, bool(vopt.get("vertical")))
                except Exception as e:
                    self._log_error(e)
            if pp and (eng == imagen_ia.AUTO and z in engines or eng == z):
                # prompt largo y detallado: Z-Image lo entiende entero (Animagine solo lee etiquetas)
                dp = imagen_ia.drawable(pp)
                if style not in ("", "Automático", "Sin estilo") and imagen_ia.STYLES.get(style):
                    dp += " Art style: " + imagen_ia.STYLES[style] + "."      # el estilo que elegiste a mano manda
                plans.append({"engine": z, "prompt": dp, "full": pp,
                              "negative": "blurry, low quality, deformed, bad anatomy, "
                              "extra fingers, text, letters, title, watermark, signature, logo"})
                continue
            # sin escritor (o motor elegido a mano): el camino de siempre
            sp = (pp or (imagen_ia.scene_prompt(txt, "", model) if model else "") or
                  "atmospheric cinematic background scene, beautiful landscape, soft light, no people")
            plans.append(imagen_ia.plan(sp, style, eng, model=model, have=engines, already_english=True))
        imagen_ia.free_ollama_gpu()
        if imagen_ia.start(say=lambda m, f: prog(m.rstrip("…"), f), cancel=self.cancel):
            self._ia_started = True
        for k, ((p, _t), pl) in enumerate(zip(todo, plans)):
            imagen_ia.generate(pl["prompt"], p, pl["engine"], (w, h), "", negative=pl["negative"], cancel=self.cancel,
                               say=lambda m, f, k=k: prog(f"Dibujando fondo {k + 1} de {len(todo)}", (k + f) / len(todo)))
            try:
                with open(p[:-4] + ".txt", "w", encoding="utf-8") as fh:
                    fh.write(pl["engine"] + " | " + pl.get("full", pl["prompt"]))
            except OSError:
                pass
        return out

    def _prompt_writer(self):
        """Quién escribe los prompts de las imágenes: tu API (Gemini, Groq…) si está configurada; si no, tu IA local
        (Qwen). Devuelve (función(sistema, usuario) -> texto, nombre) o (None, "")."""
        try:
            c = self._ai_conf()
            if (c.get("key") or not c.get("needs_key")) and c.get("model") and c.get("url") and \
                    not c["provider"].startswith("Ollama"):
                def write(system, user, c=c):
                    return ai.chat(c["url"], c["key"], c["model"],
                                   [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                   temperature=0.6, timeout=180, fallback=c.get("fallback", ""))
                return write, "Tu API (" + c["provider"].split(" (")[0] + ")"
        except Exception as e:
            self._log_error(e)
        w = imagen_ia.local_writer()
        return (w, "Tu IA (Qwen)") if w else (None, "")

    def _make_thumb(self, audio_path, title, chapter_names, chunks, vopt):
        """Miniatura automática junto al audio/video: «<nombre>_miniatura.png». Si falla, se avisa y se sigue."""
        import miniatura_auto
        out = os.path.splitext(audio_path)[0] + "_miniatura.png"

        def say(msg, f=None):
            self.after(0, lambda: (self.status(f"{msg} {os.path.basename(out)}"),
                                   self.pb.set(f) if f is not None else None))
        try:
            if not imagen_ia.available_engines():
                raise RuntimeError("falta el motor de imágenes")
            write, who = self._prompt_writer()
            if not write:
                raise RuntimeError("no hay quién escriba el prompt (configura tu API o abre Ollama)")
            story = vopt.get("doc_title") or title
            text = " ".join(c.text for c in (chunks or [])[:60])[:6000]
            say(f"{who} escribe el prompt de la miniatura…", 0)
            bible = imagen_ia.story_bible(text, story, write)
            label = miniatura_auto.chapter_label(chapter_names) if len(chapter_names) else ""
            if imagen_ia.start(say=lambda m, f: say(m, f), cancel=self.cancel):
                self._ia_started = True
            miniatura_auto.auto_cover(text, story, label, write, bible, out, say=say, cancel=self.cancel,
                                      vertical=bool(vopt.get("vertical")))
            self.after(0, lambda: self.status("Miniatura lista: " + os.path.basename(out)))
        except RuntimeError as e:
            if str(e) == "CANCELLED":
                raise audio.Cancelled()
            self._log_error(e)
            self.after(0, lambda e=e: self.status(f"No se pudo crear la miniatura ({e}); el audio está listo"))
        except Exception as e:
            self._log_error(e)
            self.after(0, lambda e=e: self.status(f"No se pudo crear la miniatura ({e}); el audio está listo"))

    def open_prompt_examples(self):
        """Abre tus ejemplos de prompts para editarlos (la IA imita su estilo en las imágenes del video)."""
        if not os.path.isfile(imagen_ia.EXAMPLES_FILE):
            os.makedirs(os.path.dirname(imagen_ia.EXAMPLES_FILE), exist_ok=True)
            with open(imagen_ia.EXAMPLES_FILE, "w", encoding="utf-8") as fh:
                fh.write("# Pega aquí tus prompts de ejemplo; sepáralos con una línea =====\n=====\n" +
                         imagen_ia._SKELETON + "\n")
        try:
            os.startfile(imagen_ia.EXAMPLES_FILE)
        except Exception as e:
            self._log_error(e)

    def _make_video(self, audio_path, title, cover, res=None, chunks=None, vopt=None):
        """Crea el MP4 junto al audio. Devuelve False si no hay FFmpeg (para no volver a intentarlo)."""
        vopt = vopt or {}
        out = os.path.splitext(audio_path)[0] + ".mp4"
        self.after(0, lambda: self.status(f"Creando el video {os.path.basename(out)}…"))
        use_img = vopt.get("bg", "").startswith("Mi portada") and cover and os.path.isfile(cover)

        def prog(msg, f):
            self.after(0, lambda: (self.pb.set(f), self.status(f"{msg} {os.path.basename(out)}… {int(f * 100)} %")))
        bg, images = vopt.get("bg", "Estrellas"), None
        if bg == AI_BG:
            try:
                images = self._ai_backgrounds(title, res, chunks, vopt, prog)
            except RuntimeError as e:
                if str(e) == "CANCELLED":
                    raise audio.Cancelled()
                self._log_error(e)
                self.after(0, lambda e=e: self.status(f"No se pudieron crear las imágenes ({e}); uso el fondo Estrellas"))
                bg = "Estrellas"
            except Exception as e:
                self._log_error(e)
                self.after(0, lambda e=e: self.status(f"No se pudieron crear las imágenes ({e}); uso el fondo Estrellas"))
                bg = "Estrellas"
        try:
            video.make_novel_video(audio_path, out, res=res, chunks=chunks, chapter_names=vopt.get("chapters", []),
                                   title=title, background=bg, image=cover if use_img else None, images=images,
                                   colors=vopt.get("colors", {}), subs=vopt.get("subs", True),
                                   names=vopt.get("names", True), vertical=vopt.get("vertical", False),
                                   cache_dir=os.path.join(CFG_DIR, "fondos"), cancel=self.cancel,
                                   progress=lambda m, f: prog(m.rstrip("…"), f))
            return True
        except RuntimeError as e:
            if str(e) == "FFMPEG_MISSING":
                self.after(0, self._ask_install_ffmpeg)
                return False
            if str(e) == "CANCELLED":
                raise audio.Cancelled()
            raise

    def _ask_install_ffmpeg(self):
        if messagebox.askyesno(APP, "Para crear el video MP4 hace falta FFmpeg (gratis) y no está instalado.\n\n"
                                    "¿Instalarlo ahora con winget? Se abre una ventana negra; cuando termine, vuelve a "
                                    "pulsar Generar. El audio ya quedó guardado."):
            try:
                import subprocess
                subprocess.Popen(["cmd", "/c", "winget install -e --id Gyan.FFmpeg --accept-package-agreements "
                                  "--accept-source-agreements & pause"],
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            except Exception as e:
                messagebox.showerror(APP, f"No se pudo abrir winget: {e}\n\nDescarga FFmpeg de gyan.dev/ffmpeg.")

    def pick_cover(self):
        p = filedialog.askopenfilename(title="Portada del video",
                                       filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if p:
            self.v_cover.set(p)
            self._autosave()

    def _finished(self, path, chunks, res, msg, secs):
        self._end_job()
        self.pb.set(1)
        m, s = divmod(int(res.duration if res else 0), 60)
        self.status(f"{msg} · {m}:{s:02d} de audio · {int(secs)} s")
        self.last_output = (path, chunks, res)
        for c in chunks or []:
            self.heard.add(c.src_start)
        self.schedule_plan(300)
        if path and messagebox.askyesno(APP, f"{msg}\nDuración: {m} min {s} s\n\n¿Escucharlo ahora?"):
            self._play_file(path, chunks, res)

    # ------------------------------------------------------------ trabajos
    def _progress(self, d, t):
        self.after(0, lambda: self._progress_ui(d, t))

    def _progress_ui(self, d, t):
        if t:
            self.pb.set(d / t)
        el = time.time() - getattr(self, "_t0", time.time())
        eta = el / d * (t - d) if d else 0
        self.status(f"Fragmento {d}/{t} · faltan ~{int(eta // 60)}:{int(eta % 60):02d}")

    def _start_job(self, text):
        self.busy = True
        self.cancel.clear()
        self.pb.set(0)
        self.status(text)
        self.btn_cancel.configure(state="normal")
        self.btn_gen.configure(state="disabled")
        self._t0 = time.time()

    def _end_job(self):
        self.busy = False
        self.btn_cancel.configure(state="disabled")
        self.btn_gen.configure(state="normal")

    def _job_error(self, e):
        self._end_job()
        self._log_error(e)
        msg = str(e)
        if any(k in msg for k in ("Cannot connect", "getaddrinfo", "ClientConnector", "Temporary failure",
                                  "speech.platform")):
            msg = ("No hay conexión con el servicio de voces de Microsoft.\n"
                   "Revisa tu internet y vuelve a intentarlo.\n\nDetalle: " + msg)
        self.status("Error")
        messagebox.showerror(APP, msg[:1500])

    @staticmethod
    def _log_error(e):
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            with open(os.path.join(CFG_DIR, "error.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("\n[%Y-%m-%d %H:%M:%S]\n"))
                f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        """Paracaídas: si algo falla dentro de un botón o un evento, se anota en error.log y la app sigue abierta."""
        self._log_error(val)
        try:
            self.status(f"Algo falló ({exc.__name__}); quedó anotado en el registro. La app sigue funcionando.")
        except Exception:
            pass

    def cancel_job(self):
        self.cancel.set()
        if self.play:
            self.stop_all()
        self.status("Cancelando…")

    def status(self, text):
        self.lbl_status.configure(text=text if len(text) <= 120 else text[:117] + "…")

    def on_close(self):
        self.cancel.set()
        self._store()
        self.stop_all()
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kuon.AudioNovelaStudio")
        except Exception:
            pass
    threading.Thread(target=_clean_stream_leftovers, daemon=True).start()
    # errores en hilos de trabajo: se anotan en error.log en vez de perderse
    threading.excepthook = lambda a: Studio._log_error(a.exc_value) if a.exc_value is not None else None
    app = Studio()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(os.path.join(CFG_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("\n[%Y-%m-%d %H:%M:%S] ") + traceback.format_exc())
        raise
