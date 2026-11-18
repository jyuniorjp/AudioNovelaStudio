# -*- coding: utf-8 -*-
"""
Refuerzo opcional con un modelo de lenguaje local vía Ollama (http://127.0.0.1:11434).
Todo corre en tu PC; si Ollama no está abierto, la app usa solo Kuon IA.
"""
import itertools
import json
import re
import urllib.request

URL = "http://127.0.0.1:11434"


def _req(path, payload=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(URL + path, data=data,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def models():
    try:
        d = _req("/api/tags", timeout=2)
        return [m["name"] for m in d.get("models", [])]
    except Exception:
        return None


def _chat(model, system, user):
    d = _req("/api/chat", {
        "model": model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    msg = d.get("message") if isinstance(d, dict) else None
    txt = (msg.get("content") if isinstance(msg, dict) else None) or "{}"
    try:
        r = json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        try:
            r = json.loads(m.group(0)) if m else {}
        except Exception:
            r = {}
    return r if isinstance(r, dict) else {}       # un modelo pequeño a veces responde una lista o un texto


def profile(model, text, names):
    """Género y edad de cada personaje leyendo fragmentos donde aparece."""
    out = {}
    for i in range(0, len(names), 8):
        batch = names[i:i + 8]
        parts = []
        for nm in batch:
            snips = [text[max(0, m.start() - 160): m.end() + 160].replace("\n", " ")
                     for m in itertools.islice(re.finditer(r"\b" + re.escape(nm) + r"\b", text), 4)]
            parts.append(f"### {nm}\n" + "\n".join(f"- …{s}…" for s in snips))
        sys_p = ("Eres un analista literario. Lee los fragmentos de una novela y deduce para cada "
                 "personaje su género (M o F) y su edad aparente (niño, joven, adulto, anciano). "
                 "Responde solo JSON: {\"Nombre\": {\"genero\": \"M\", \"edad\": \"joven\"}}")
        try:
            res = _chat(model, sys_p, "\n\n".join(parts))
        except Exception:       # un lote que falla no tumba los demás
            continue
        for nm in batch:
            v = res.get(nm)
            if not isinstance(v, dict):
                continue
            g = str(v.get("genero", v.get("gender", ""))).upper()[:1]
            e = str(v.get("edad", v.get("age", ""))).lower()
            if g in ("M", "F") or e in ("niño", "joven", "adulto", "anciano"):
                out[nm] = {"gender": g if g in ("M", "F") else None,
                           "age": e if e in ("niño", "joven", "adulto", "anciano") else None}
    return out


def attribute(model, text, segs, names, progress=None, cancel=None, weak=("", "conversación", "pronombre", "sujeto de la escena", "nombre nuevo", "párrafo previo")):
    """Decide quién dice los diálogos dudosos. Devuelve {key: nombre}."""
    todo = [s for s in segs if s.kind == "dlg" and s.source in weak]
    out = {}
    names_l = ", ".join(names) or "(ninguno detectado)"
    sys_p = ("Eres un experto en novelas. Para cada línea de diálogo numerada, di qué personaje la "
             "pronuncia según el contexto. Usa exactamente uno de estos nombres: " + names_l +
             ". Si habla un grupo usa \"grupo\" y si no se puede saber usa \"?\". "
             "Responde solo JSON: {\"1\": \"Nombre\", \"2\": \"?\"}")
    for i in range(0, len(todo), 10):
        if cancel and cancel.is_set():
            break
        batch = todo[i:i + 10]
        lo = max(0, batch[0].start - 1200)
        hi = min(len(text), batch[-1].end + 300)
        ctx = text[lo:hi]
        items = "\n".join(f"{n + 1}. \"{s.text[:160]}\"" for n, s in enumerate(batch))
        try:
            res = _chat(model, sys_p, f"CONTEXTO:\n{ctx}\n\nDIÁLOGOS A ATRIBUIR:\n{items}")
        except Exception:
            continue
        for n, s in enumerate(batch):
            v = str(res.get(str(n + 1), "?")).strip()
            if v in names:
                out[s.key] = v
            elif v.lower() == "grupo":
                out[s.key] = ""
        if progress:
            progress(min(i + 10, len(todo)), len(todo))
    return out
