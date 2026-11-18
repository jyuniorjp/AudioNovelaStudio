# -*- coding: utf-8 -*-
"""Preguntas de repaso con IA (práctica de recuperación: la técnica de estudio con más evidencia).

Para que la IA no invente datos, cada pregunta trae una cita textual del capítulo y la app comprueba que esa
cita existe de verdad en el texto antes de mostrarla (idea del informe «Audio Novela Studio para estudio
universitario»). La misma lógica está en app.js (quizPrompt / quizParse) para el teléfono.
"""
import json
import re
import unicodedata

SYSTEM = ("Eres un profesor universitario que prepara preguntas de repaso en español a partir de un texto. "
          "Solo usas información que está en el texto. Respondes únicamente con JSON.")


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ]+", " ", s).strip()


def prompt(text, n=5):
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"Escribe {n} preguntas de opción múltiple para repasar este texto (conceptos, causas, fórmulas, "
                "definiciones, pasos de un procedimiento). Cada pregunta con 4 opciones, una sola correcta, opciones "
                "incorrectas creíbles. Para cada una copia una cita TEXTUAL corta (10 a 30 palabras) del texto que "
                "pruebe la respuesta.\nDevuelve SOLO un JSON así: "
                '[{"pregunta": "…", "opciones": ["…", "…", "…", "…"], "correcta": 0, "cita": "…"}]'
                f"\n\nTEXTO:\n{text}"}]


def parse(reply, text):
    """-> (preguntas verificadas, descartadas). Una pregunta se descarta si su cita no aparece en el texto."""
    m = re.search(r"\[.*\]", reply or "", re.S)
    if not m:
        return [], 0
    try:
        data = json.loads(m.group(0))
    except ValueError:
        try:
            data = json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(0)))
        except ValueError:
            return [], 0
    ntext = " " + _norm(text) + " "
    ok, bad = [], 0
    for q in data if isinstance(data, list) else []:
        try:
            opts = [str(o).strip() for o in q.get("opciones", [])][:6]
            c = int(q.get("correcta", -1))
            cita = str(q.get("cita", "")).strip().strip("«»\"'… ")
            preg = str(q.get("pregunta", "")).strip()
        except (AttributeError, TypeError, ValueError):
            bad += 1
            continue
        nc = _norm(cita)
        words = nc.split()
        found = len(words) >= 4 and (" " + nc + " ") in ntext
        if not found and len(words) >= 8:          # cita casi textual: basta con que aparezcan 8 palabras seguidas
            found = any(" " + " ".join(words[i:i + 8]) + " " in ntext for i in range(0, len(words) - 7))
        if preg and len(opts) >= 2 and 0 <= c < len(opts) and found:
            ok.append({"pregunta": preg, "opciones": opts, "correcta": c, "cita": cita})
        else:
            bad += 1
    return ok, bad


def cite_pos(text, cita):
    """Posición aproximada de la cita en el texto (para «▶ Escuchar esa parte»)."""
    words = [w for w in re.findall(r"\w+", cita)][:6]
    if not words:
        return -1
    pat = r"\W+".join(re.escape(w) for w in words)
    m = re.search(pat, text, re.I)
    return m.start() if m else -1
