# -*- coding: utf-8 -*-
"""Ayuda de IA solo para los diálogos dudosos de Kuon IA.

Idea tomada de la investigación (Michel et al. 2025, TTS-Story, audiobook-creator): las reglas resuelven los
diálogos con «—dijo X» y la IA solo mira los dudosos, con la lista de personajes y apodos, y responde en JSON.
Nada se aplica solo: cada sugerencia aparece en «Revisar dudosos» para que el usuario la acepte.
La misma lógica está en la APK (app.js: iaBatches / iaParse) para que las dos versiones respondan igual.
"""
import json
import re
import unicodedata

SYSTEM = ("Eres un editor experto en novelas y fanfics en español. Decides quién pronuncia cada diálogo marcado "
          "de un fragmento. Usas solo nombres de la lista de personajes. Respondes únicamente con JSON.")

INSTR = ("En el TEXTO, cada diálogo que hay que decidir está entre [Dn] y [/Dn]. Para cada n, di quién lo dice.\n"
         "- Usa exactamente un nombre de la lista de PERSONAJES.\n"
         "- Escribe «narrador» si es narración, un pensamiento sin dueño claro o lo dice un grupo o alguien sin nombre.\n"
         "- Escribe «?» solo si de verdad no se puede saber.\n"
         "Fíjate en quién habló antes y a quién responde, en los pronombres (él/ella), en el género de los adjetivos "
         "(cansado/cansada), en los apodos y en quién está en la escena.\n"
         'Devuelve SOLO un JSON así: {"1": "Nombre", "2": "narrador", "3": "?"}')


def plain(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn").strip()


def is_doubtful(s, medium=False):
    """Diálogo dudoso: Kuon IA dudó, nadie lo dice, o la confianza es baja. Las correcciones a mano no cuentan."""
    if s.kind != "dlg" or s.source == "manual":
        return False
    if s.guess or not s.speaker:
        return True
    return s.conf < (0.7 if medium else 0.61)


def characters_desc(chars, aliases):
    """«- Rias (mujer; también: Buchou)» por cada personaje, en orden de importancia."""
    by_canon = {}
    for a, c in (aliases or {}).items():
        if a and c and plain(a) != plain(c):
            by_canon.setdefault(c, []).append(a)
    out = []
    for name, ch in chars.items():
        g = getattr(ch, "gender", "")
        gs = {"F": "mujer", "M": "hombre"}.get(g, "")
        al = sorted(set(by_canon.get(name, [])))[:6]
        extra = "; ".join(x for x in (gs, ("también: " + ", ".join(al)) if al else "") if x)
        out.append(f"- {name}" + (f" ({extra})" if extra else ""))
    return "\n".join(out[:60])


def build_batches(text, items, max_chars=6000, before=700, after=200, max_marks=20):
    """items: [(clave, inicio, fin)] -> [(texto con marcas [Dn]…[/Dn], {n: clave})]."""
    groups = []
    for key, a, b in sorted(items, key=lambda x: x[1]):
        lo, hi = max(0, a - before), min(len(text), b + after)
        g = groups[-1] if groups else None
        if g and lo <= g["hi"] + 50 and max(hi, g["hi"]) - g["lo"] <= max_chars and len(g["marks"]) < max_marks:
            g["hi"] = max(g["hi"], hi)
            g["marks"].append((key, a, b))
        else:
            groups.append({"lo": lo, "hi": hi, "marks": [(key, a, b)]})
    out = []
    for g in groups:
        frag, ids = text[g["lo"]:g["hi"]], {}
        for n, (key, a, b) in reversed(list(enumerate(g["marks"], 1))):
            ra, rb = a - g["lo"], b - g["lo"]
            frag = frag[:ra] + f"[D{n}]" + frag[ra:rb] + f"[/D{n}]" + frag[rb:]
            ids[str(n)] = key
        out.append((("…" if g["lo"] > 0 else "") + frag + ("…" if g["hi"] < len(text) else ""), ids))
    return out


def messages(passage, chars_desc):
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"PERSONAJES:\n{chars_desc}\n\n{INSTR}\n\nTEXTO:\n{passage}"}]


def parse(reply, names, aliases=None):
    """Respuesta de la IA -> {n: nombre canónico | "" (narrador)}. Ignora «?» y nombres que no existen."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        try:
            data = json.loads(re.sub(r",\s*}", "}", m.group(0).replace("'", '"')))
        except ValueError:
            return {}
    canon = {plain(n): n for n in names}
    for a, c in (aliases or {}).items():
        if c in names:
            canon.setdefault(plain(a), c)
    out = {}
    for k, v in data.items():
        k = re.sub(r"\D", "", str(k))
        v = plain(str(v)).strip(" .«»\"'")
        if not k or not v or v == "?":
            continue
        if v in ("narrador", "narracion", "narrator", "grupo", "nadie"):
            out[k] = ""
        elif v in canon:
            out[k] = canon[v]
        else:
            hit = [c for p, c in canon.items() if p.split()[0] == v.split()[0]]   # «Rias Gremory» -> «Rias»
            if len(set(hit)) == 1:
                out[k] = hit[0]
    return out


def suggest(text, items, chars, aliases, chat_fn, on_progress=None, cancel=None):
    """items: [(clave, inicio, fin)]; chat_fn(messages) -> texto. Devuelve {clave: nombre | ""}."""
    names = list(chars)
    desc = characters_desc(chars, aliases)
    batches = build_batches(text, items)
    out = {}
    for i, (passage, ids) in enumerate(batches, 1):
        if cancel is not None and cancel.is_set():
            break
        if on_progress:
            on_progress(i, len(batches))
        got = parse(chat_fn(messages(passage, desc)), names, aliases)
        for n, name in got.items():
            if n in ids:
                out[ids[n]] = name
    return out
