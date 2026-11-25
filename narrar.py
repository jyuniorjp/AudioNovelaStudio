# -*- coding: utf-8 -*-
"""
Audio Novela Studio sin ventana: narra un texto con el mismo motor de la app (Kuon IA, reparto de voces,
emociones, pausas) y usa tus ajustes guardados (narrador, velocidad, reparto y pronunciación de cada obra).
Pensado para que Claude (u otro programa) genere audio directamente.

  python narrar.py historia.txt -o historia.mp3            # cualquier formato que lea la app (txt, docx, pdf, epub…)
  python narrar.py --texto "—Hola —dijo Ana." -o prueba.mp3
  python narrar.py cap.txt -o cap.mp3 --voz es-MX-JorgeNeural --velocidad 1.2 --solo-narrador --srt
  python narrar.py cap.txt -o cap.mp3 --modo offline       # voz sin internet (usa la tarjeta gráfica si hay)
  python narrar.py --voces es                              # lista de voces en línea (Microsoft) de un idioma
  python narrar.py --hardware                              # qué tarjeta/procesador se usa
Desde el programa instalado: AudioNovelaStudio.exe --narrar <los mismos argumentos>
Al terminar escribe una línea JSON con el resultado (archivo, duración, voces usadas).
"""
import argparse
import dataclasses
import json
import os
import sys
import time

CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AudioNovelaStudio")
CFG_FILE = os.path.join(CFG_DIR, "config.json")
MEM_FILE = os.path.join(CFG_DIR, "kuon_memoria.json")


def _out():
    """En el .exe sin consola no hay stdout: se escribe en narrar.log de la carpeta de ajustes."""
    if sys.stdout is None:
        os.makedirs(CFG_DIR, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(CFG_DIR, "narrar.log"), "a", encoding="utf-8")
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _base_memory():
    for d in (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None,
              os.path.dirname(os.path.abspath(__file__)), getattr(sys, "_MEIPASS", None)):
        if d and os.path.exists(os.path.join(d, "kuon_base.json")):
            return _load_json(os.path.join(d, "kuon_base.json")) or None
    return None


def _project_of(path):
    import re
    if not path:
        return "_sin_titulo"
    d = os.path.dirname(os.path.abspath(path))
    if re.search(r"cap[ií]tulo|chapter|^cap\b|^\d+$|bloque|parte", os.path.basename(d), re.I):
        d = os.path.dirname(d)
    return os.path.basename(d) or "_sin_titulo"


def main(argv=None):
    _out()
    ap = argparse.ArgumentParser(prog="narrar", description="Narra un texto con el motor de Audio Novela Studio.")
    ap.add_argument("entrada", nargs="?", help="archivo a narrar (o - para leer de la entrada estándar)")
    ap.add_argument("-o", "--salida", help="archivo de audio (.mp3 o .wav)")
    ap.add_argument("--texto", help="texto a narrar en vez de un archivo")
    ap.add_argument("--voz", help="voz del narrador (p. ej. es-MX-JorgeNeural)")
    ap.add_argument("--velocidad", type=float, help="1.0 = normal (la app usa la de tus ajustes)")
    ap.add_argument("--tono", type=int, help="tono del narrador, -50 a 50")
    ap.add_argument("--modo", choices=["auto", "online", "offline"], help="online = voces de Microsoft; "
                    "offline = voz natural sin internet; auto = online si hay internet")
    ap.add_argument("--solo-narrador", action="store_true", help="una sola voz para todo, diálogos incluidos")
    ap.add_argument("--multivoz", action="store_true", help="una voz por personaje aunque tus ajustes digan lo contrario")
    ap.add_argument("--sin-emociones", action="store_true")
    ap.add_argument("--proyecto", help="obra cuyo reparto/pronunciación guardados usar (por defecto, la carpeta)")
    ap.add_argument("--musica", help="música de fondo (mp3/wav)")
    ap.add_argument("--srt", action="store_true", help="escribe también los subtítulos .srt")
    ap.add_argument("--hilos", type=int, help="fragmentos en paralelo (por defecto, los de tus ajustes)")
    ap.add_argument("--voces", nargs="?", const="es", metavar="IDIOMA", help="lista las voces en línea y sale")
    ap.add_argument("--hardware", action="store_true", help="muestra la tarjeta/procesador detectado y sale")
    a = ap.parse_args(argv)

    import hardware
    if a.hardware:
        print(hardware.summary())
        print(json.dumps(hardware.info(), ensure_ascii=False))
        return 0
    import voices as vx
    if a.voces:
        vs, online = vx.load_voices()
        for v in sorted(vx.for_lang(vs, a.voces, multi=False), key=lambda v: v.name):
            print(f"{v.name}\t{'mujer' if v.gender == 'Female' else 'hombre'}")
        if not online:
            print("(sin internet: lista reducida)", file=sys.stderr)
        return 0

    if a.texto is not None:
        text, name = a.texto, "texto"
    elif a.entrada == "-":
        text, name = sys.stdin.read(), "entrada"
    elif a.entrada:
        import docs
        text, name = docs.read_document(a.entrada), os.path.basename(a.entrada)
    else:
        ap.error("falta el archivo de entrada o --texto")
    if not a.salida:
        base = os.path.splitext(a.entrada)[0] if a.entrada and a.entrada != "-" else os.path.join(os.getcwd(), "narracion")
        a.salida = base + ".mp3"
    fmt = "wav" if a.salida.lower().endswith(".wav") else "mp3"

    import audio
    import brain
    import docs
    cfg = _load_json(CFG_FILE)
    fields = {f.name for f in dataclasses.fields(audio.Settings)}
    st = audio.Settings(**{k: v for k, v in cfg.items() if k in fields and k not in ("cast", "pron", "emo_over")})
    st.pause_title = int(st.pause_scene * 0.75)
    if not os.path.exists(st.music_path or ""):
        st.music_path = ""
    proj = a.proyecto or (_project_of(a.entrada) if a.entrada and a.entrada != "-" else "_sin_titulo")
    pj = cfg.get("projects", {}).get(proj, {})
    ds = cfg.get("docs", {}).get(name, {})
    if a.voz:
        st.narrator = a.voz
    if a.velocidad:
        st.speed = a.velocidad
    if a.tono is not None:
        st.pitch = a.tono
    if a.modo:
        st.voice_mode = a.modo
    if a.solo_narrador:
        st.character_voices = False
    elif a.multivoz:
        st.character_voices = True
    if a.sin_emociones:
        st.emotions = st.scene_moods = False
    if a.musica:
        st.music_path = a.musica

    mem = brain.new_memory()
    mem.update(_load_json(MEM_FILE))
    brain.merge_base(mem, _base_memory(), proj)
    segs, chars, _ = brain.analyze(text, ds.get("overrides", {}), pj.get("aliases") or ds.get("aliases", {}),
                                   memory=mem, project=proj, threshold=float(cfg.get("threshold", 0.55)), doc=name)
    vs, _ = vx.load_voices()
    cast = vx.auto_cast(list(chars.values()), vs, cfg.get("lang", "es"), st.narrator, dict(pj.get("cast") or {}),
                        neutral=cfg.get("variant", True))
    st = dataclasses.replace(st, cast=cast, pron=dict(pj.get("pron", {})), emo_over=dict(ds.get("emo", {})))
    chunks = audio.plan(text, segs, docs.split_chapters(text), st)
    if not chunks:
        print(json.dumps({"ok": False, "error": "no hay texto para narrar"}, ensure_ascii=False))
        return 1
    offline = audio.use_offline(st)
    print(f"Narrando {len(chunks)} fragmentos con {'la voz sin internet' if offline else 'voces de Microsoft'} · "
          f"{hardware.summary()}", file=sys.stderr, flush=True)
    t0 = time.time()
    last = [0.0]

    def prog(d, t):
        if time.time() - last[0] > 2 or d == t:
            last[0] = time.time()
            print(f"  {d}/{t}", file=sys.stderr, flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.salida)), exist_ok=True)
    res = audio.render(chunks, a.salida, st, fmt, progress=prog, workers=a.hilos or int(cfg.get("workers", 4)))
    out = {"ok": True, "archivo": os.path.abspath(a.salida), "segundos": round(res.duration, 1),
           "tardo": round(time.time() - t0, 1), "fuente": "sin internet" if offline else "Microsoft",
           "narrador": st.narrator, "multivoz": st.character_voices,
           "personajes": {k: v["voice"] for k, v in cast.items()}}
    if a.srt:
        srt = os.path.splitext(a.salida)[0] + ".srt"
        audio.write_srt(res, srt)
        out["srt"] = os.path.abspath(srt)
    if audio.SKIPPED:
        out["fragmentos_saltados"] = audio.SKIPPED[:10]
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
