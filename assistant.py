# -*- coding: utf-8 -*-
"""
Asistente de Audio Novela Studio: un modelo de lenguaje con el que puedes hablar de tu historia.

Funciona con cualquier servicio compatible con la API de OpenAI (chat/completions):
Gemini, OpenRouter, Groq, OpenAI, DeepSeek, Ollama (sin internet) u otro que indiques.
Cada persona pone su propia clave; se guarda cifrada con la cuenta de Windows (DPAPI).

El asistente puede proponer cambios en un bloque ```acciones``` (JSON). La app los muestra y
solo los aplica si tú los aceptas; todo se puede deshacer.
"""
import base64
import time
import json
import re
import sys
import urllib.error
import urllib.request

PROVIDERS = {
    "Gemini (Google)": {"url": "https://generativelanguage.googleapis.com/v1beta/openai",
                        "model": "gemini-2.5-flash", "key": True,
                        "help": "Clave gratis en aistudio.google.com › Get API key."},
    "OpenRouter": {"url": "https://openrouter.ai/api/v1", "model": "openrouter/auto", "key": True,
                   "help": "Clave en openrouter.ai › Keys. Los modelos que terminan en «:free» no cobran."},
    "Groq": {"url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "key": True,
             "help": "Clave gratis en console.groq.com › API Keys. Muy rápido."},
    "OpenAI": {"url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "key": True,
               "help": "Clave en platform.openai.com › API keys (de pago)."},
    "DeepSeek": {"url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": True,
                 "help": "Clave en platform.deepseek.com (de pago, barato)."},
    "Ollama (sin internet)": {"url": "http://127.0.0.1:11434/v1", "model": "", "key": False,
                              "help": "Corre en tu PC. Instala Ollama y un modelo, por ejemplo: ollama pull qwen2.5:7b"},
    "Otro (compatible con OpenAI)": {"url": "", "model": "", "key": True,
                                     "help": "Escribe la dirección base que termina en /v1 y el nombre del modelo."},
}


# ---------------------------------------------------------------- clave guardada cifrada
def protect(text):
    """Cifra la clave con la cuenta de Windows del usuario (solo se puede leer en esta PC y este usuario)."""
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class BLOB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
            src = BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
            out = BLOB()
            if ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
                data = ctypes.string_at(out.pbData, out.cbData)
                ctypes.windll.kernel32.LocalFree(out.pbData)
                return "dpapi:" + base64.b64encode(data).decode()
        except Exception:
            pass
    return "b64:" + base64.b64encode(raw).decode()


def unprotect(blob):
    if not blob:
        return ""
    try:
        kind, data = blob.split(":", 1)
        raw = base64.b64decode(data)
        if kind == "b64":
            return raw.decode("utf-8")
        if kind == "dpapi" and sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class BLOB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
            src = BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
            out = BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
                data = ctypes.string_at(out.pbData, out.cbData)
                ctypes.windll.kernel32.LocalFree(out.pbData)
                return data.decode("utf-8")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- llamadas
class AIError(Exception):
    def __init__(self, msg, code=0, detail=""):
        super().__init__(msg)
        self.code, self.detail = code, detail


def _request(url, key, payload=None, timeout=120):
    headers = {"Content-Type": "application/json", "User-Agent": "AudioNovelaStudio"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if "openrouter.ai" in url:
        headers["HTTP-Referer"] = "https://audionovela.studio"
        headers["X-Title"] = "Audio Novela Studio"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        msg = {400: "El servicio rechazó la petición (revisa el modelo elegido)",
               401: "La clave no es válida", 403: "La clave no tiene permiso para este modelo",
               404: "No existe ese modelo o esa dirección", 429: "Llegaste al límite de uso; espera un poco",
               500: "El servicio tuvo un error interno", 503: "El servicio está saturado; prueba en un rato"
               }.get(e.code, f"Error {e.code} del servicio")
        detail = re.sub(r"\s+", " ", body)
        raise AIError(msg, e.code, detail)
    except urllib.error.URLError as e:
        raise AIError("No hay conexión con el servicio (" + str(getattr(e, "reason", e)) + ")")
    except TimeoutError:
        raise AIError("El servicio tardó demasiado en responder")


# Modelos que no sirven para conversar: voz (TTS), imágenes, video, embeddings, audio en vivo…
NOT_CHAT = ("tts", "embedding", "embed", "imagen", "image", "veo", "aqa", "audio", "live", "transcrib",
            "whisper", "dall-e", "moderation", "rerank", "lyria", "robotics", "computer-use")


def is_chat_model(mid):
    m = mid.lower()
    return not any(x in m for x in NOT_CHAT)


# modelos recomendados primero (rápidos, buenos y, si hay, gratis); el resto en orden alfabético
PREFERRED = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.5-pro",
             "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant", "openrouter/auto",
             "gpt-4o-mini", "gpt-4.1-mini", "deepseek-chat", "qwen2.5:7b", "llama3.1:8b"]


def rank_models(ms, url=""):
    def key(m):
        free = m.endswith(":free") or ("openrouter" not in url)
        pref = PREFERRED.index(m) if m in PREFERRED else len(PREFERRED)
        return (pref, not free, "preview" in m or "exp" in m, m)
    return sorted(ms, key=key)


def list_models(base, key):
    d = _request(base.rstrip("/") + "/models", key, timeout=20)
    items = d.get("data") or d.get("models") or []
    out = []
    for m in items:
        mid = m.get("id") or m.get("name") or ""
        if mid.startswith("models/"):
            mid = mid[7:]
        if mid and is_chat_model(mid):
            out.append(mid)
    return sorted(set(out))


def _one(base, key, model, messages, temperature, timeout):
    d = _request(base.rstrip("/") + "/chat/completions", key,
                 {"model": model, "messages": messages, "temperature": temperature}, timeout=timeout)
    try:
        return d["choices"][0]["message"]["content"] or ""
    except Exception:
        raise AIError("Respuesta inesperada del servicio: " + json.dumps(d)[:200])


def _no_system(messages):
    """Algunos modelos (Gemma, varios gratis) no aceptan instrucciones de sistema: van dentro del primer mensaje."""
    sys_txt = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [dict(m) for m in messages if m["role"] != "system"]
    if sys_txt and rest:
        rest[0]["content"] = "INSTRUCCIONES:\n" + sys_txt + "\n\n---\n\n" + rest[0]["content"]
    return rest


def chat(base, key, model, messages, temperature=0.4, timeout=120, fallback="", on_note=None):
    """Conversa con reintentos: si el modelo está saturado (503/429) espera y repite; si sigue igual,
    usa el modelo de respaldo. Si el modelo no admite instrucciones de sistema, las mete en el mensaje."""
    if not base:
        raise AIError("Falta la dirección del servicio")
    if not model:
        raise AIError("Elige un modelo (botón «Modelos»)")
    if not is_chat_model(model):
        raise AIError(f"«{model}» es un modelo de voz o de imágenes y no puede conversar. Elige uno de texto "
                      "(por ejemplo uno «flash» sin «tts»).")
    last = None
    for mdl in [model] + ([fallback] if fallback and fallback != model else []):
        msgs = messages
        for intento in range(3):
            try:
                return _one(base, key, mdl, msgs, temperature, timeout)
            except AIError as e:
                last = e
                low = (e.detail or "").lower()
                if e.code == 400 and ("developer instruction" in low or "system" in low) and msgs is messages:
                    msgs = _no_system(messages)
                    continue
                if e.code in (429, 500, 502, 503, 504) and intento < 2:
                    if on_note:
                        on_note(f"{mdl} está saturado; reintento en {2 + 4 * intento} s…")
                    time.sleep(2 + 4 * intento)
                    continue
                break
        if fallback and mdl != fallback and on_note and last and last.code in (429, 500, 502, 503, 504):
            on_note(f"Pruebo con el modelo de respaldo {fallback}…")
    raise last


# ---------------------------------------------------------------- acciones propuestas
ACTIONS_HELP = """Puedes proponer cambios en la app. Si lo haces, al FINAL de tu respuesta añade un bloque así:
```acciones
[{"accion": "asignar", "linea": "L3", "nombre": "Hestia"},
 {"accion": "unir", "nombre": "Mefisto", "con": "Mefistófeles"},
 {"accion": "genero", "nombre": "Kuon", "genero": "F"},
 {"accion": "edad", "nombre": "Henrietta", "edad": "joven"},
 {"accion": "no_es_personaje", "nombre": "Ventanilla"},
 {"accion": "color", "nombre": "Ddraig", "color": "#22c55e"},
 {"accion": "pronunciar", "palabra": "Ryūjin", "como": "Riuyín"},
 {"accion": "emocion", "linea": "L3", "emocion": "susurro"}]
```
Reglas: usa solo los identificadores de línea (L1, L2…) y los nombres que aparecen en el contexto; edad es
niño, joven, adulto o anciano; emoción es neutral, grito, susurro, pensamiento, triste, alegre, enojo, duda,
frio, miedo o ternura. No inventes cambios que el usuario no pidió, salvo que te pida revisar el reparto.
El usuario verá los cambios y decidirá si los aplica."""

SYSTEM = """Eres Kuon, la asistente de Audio Novela Studio, una app que narra novelas y fanfics en español con voces
distintas para cada personaje. Ayudas al usuario con su historia y con la narración: quién dice cada diálogo,
el reparto de voces, el género y la edad de los personajes, la pronunciación de nombres extranjeros, cómo
escribir para que la narración suene mejor (incisos con verbo y nombre, rayas de diálogo, títulos «Capítulo N»,
separadores ***), y también puedes conversar sobre la trama, los personajes o ideas de escritura.
Responde en español, claro y breve, sin relleno. Si no sabes algo del texto, dilo.

""" + ACTIONS_HELP


def parse_actions(text):
    """Separa el texto visible y la lista de acciones del bloque ```acciones```."""
    m = re.search(r"```\s*acciones\s*(\[.*?\])\s*```", text, re.S | re.I)
    if not m:
        m = re.search(r"```(?:json)?\s*(\[\s*\{.*?\"accion\".*?\])\s*```", text, re.S | re.I)
    if not m:
        return text.strip(), []
    visible = (text[:m.start()] + text[m.end():]).strip()
    try:
        acts = json.loads(m.group(1))
    except Exception:
        try:
            acts = json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(1)))
        except Exception:
            return visible, []
    return visible, [a for a in acts if isinstance(a, dict) and a.get("accion")]


def describe(a):
    k = a.get("accion")
    if k == "asignar":
        return f"{a.get('linea')}: lo dice {a.get('nombre') or 'el narrador'}"
    if k == "unir":
        return f"{a.get('nombre')} es el mismo personaje que {a.get('con')}"
    if k == "genero":
        return f"{a.get('nombre')}: {'mujer' if str(a.get('genero', '')).upper().startswith('F') else 'hombre'}"
    if k == "edad":
        return f"{a.get('nombre')}: {a.get('edad')}"
    if k == "no_es_personaje":
        return f"«{a.get('nombre')}» no es un personaje"
    if k == "color":
        return f"{a.get('nombre')}: color {a.get('color')}"
    if k == "pronunciar":
        return f"«{a.get('palabra')}» se lee «{a.get('como')}»"
    if k == "emocion":
        return f"{a.get('linea')}: emoción {a.get('emocion')}"
    return json.dumps(a, ensure_ascii=False)
