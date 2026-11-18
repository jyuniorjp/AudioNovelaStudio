# -*- coding: utf-8 -*-
"""Catálogo de voces neurales de Microsoft y reparto automático."""
import asyncio
from dataclasses import dataclass

import edge_tts

LANG_NAMES = {
    "es": "Español", "en": "Inglés", "pt": "Portugués", "fr": "Francés", "de": "Alemán",
    "it": "Italiano", "ja": "Japonés", "ko": "Coreano", "zh": "Chino", "ru": "Ruso",
    "ar": "Árabe", "hi": "Hindi", "tr": "Turco", "pl": "Polaco", "nl": "Neerlandés",
    "ca": "Catalán", "gl": "Gallego", "eu": "Euskera", "sv": "Sueco", "uk": "Ucraniano",
    "id": "Indonesio", "vi": "Vietnamita", "th": "Tailandés", "el": "Griego", "he": "Hebreo",
}

# Orden de preferencia para español neutro (latino primero, España al final)
ES_MALE = ["es-MX-JorgeNeural", "es-US-AlonsoNeural", "es-CO-GonzaloNeural", "es-PE-AlexNeural",
           "es-VE-SebastianNeural", "es-AR-TomasNeural", "es-CL-LorenzoNeural", "es-BO-MarceloNeural",
           "es-EC-LuisNeural", "es-CR-JuanNeural", "es-PA-RobertoNeural", "es-UY-MateoNeural",
           "es-PY-MarioNeural", "es-GT-AndresNeural", "es-HN-CarlosNeural", "es-NI-FedericoNeural",
           "es-SV-RodrigoNeural", "es-DO-EmilioNeural", "es-PR-VictorNeural", "es-CU-ManuelNeural",
           "es-ES-AlvaroNeural", "es-GQ-JavierNeural"]
ES_FEMALE = ["es-MX-DaliaNeural", "es-US-PalomaNeural", "es-CO-SalomeNeural", "es-PE-CamilaNeural",
             "es-VE-PaolaNeural", "es-AR-ElenaNeural", "es-CL-CatalinaNeural", "es-BO-SofiaNeural",
             "es-EC-AndreaNeural", "es-CR-MariaNeural", "es-PA-MargaritaNeural", "es-UY-ValentinaNeural",
             "es-PY-TaniaNeural", "es-GT-MartaNeural", "es-HN-KarlaNeural", "es-NI-YolandaNeural",
             "es-SV-LorenaNeural", "es-DO-RamonaNeural", "es-PR-KarinaNeural", "es-CU-BelkysNeural",
             "es-ES-ElviraNeural", "es-ES-XimenaNeural", "es-GQ-TeresaNeural"]

FALLBACK = [(n, "Male") for n in ES_MALE] + [(n, "Female") for n in ES_FEMALE] + [
    ("en-US-AndrewMultilingualNeural", "Male"), ("en-US-BrianMultilingualNeural", "Male"),
    ("en-US-AvaMultilingualNeural", "Female"), ("en-US-EmmaMultilingualNeural", "Female"),
    ("en-US-GuyNeural", "Male"), ("en-US-JennyNeural", "Female"), ("en-US-ChristopherNeural", "Male"),
    ("en-US-AriaNeural", "Female"), ("en-GB-RyanNeural", "Male"), ("en-GB-SoniaNeural", "Female"),
    ("pt-BR-AntonioNeural", "Male"), ("pt-BR-FranciscaNeural", "Female"),
    ("fr-FR-RemyMultilingualNeural", "Male"), ("fr-FR-VivienneMultilingualNeural", "Female"),
    ("fr-FR-HenriNeural", "Male"), ("fr-FR-DeniseNeural", "Female"),
    ("de-DE-FlorianMultilingualNeural", "Male"), ("de-DE-SeraphinaMultilingualNeural", "Female"),
    ("it-IT-GiuseppeMultilingualNeural", "Male"), ("it-IT-DiegoNeural", "Male"), ("it-IT-ElsaNeural", "Female"),
    ("ja-JP-KeitaNeural", "Male"), ("ja-JP-NanamiNeural", "Female"),
    ("ko-KR-InJoonNeural", "Male"), ("ko-KR-SunHiNeural", "Female"),
    ("zh-CN-YunxiNeural", "Male"), ("zh-CN-XiaoxiaoNeural", "Female"),
]


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str

    @property
    def lang(self):
        return self.name.split("-")[0]

    @property
    def locale(self):
        return "-".join(self.name.split("-")[:2])

    @property
    def multilingual(self):
        return "Multilingual" in self.name

    @property
    def short(self):
        return self.name.split("-", 2)[2].replace("MultilingualNeural", " Multi").replace("Neural", "")

    @property
    def label(self):
        g = "♂" if self.gender == "Male" else "♀"
        return f"{g} {self.short} · {self.locale}"


def load_voices():
    try:
        vs = asyncio.run(edge_tts.list_voices())
        out = [Voice(v["ShortName"], v.get("Gender", "Male")) for v in vs]
        if out:
            return out, True
    except Exception:
        pass
    return [Voice(n, g) for n, g in FALLBACK], False


def for_lang(voices, lang, multi=True):
    vs = [v for v in voices if v.lang == lang]
    if multi and lang != "en":
        vs += [v for v in voices if v.multilingual and v.lang != lang]
    return vs


# Acentos que suenan a español latino neutro (sin rioplatense, chileno, caribeño ni de España)
NEUTRAL_LOCALES = {"es-MX", "es-US", "es-CO", "es-PE", "es-EC", "es-BO", "es-CR", "es-GT", "es-HN",
                   "es-SV", "es-NI", "es-PA", "es-VE"}
_NEUTRAL_ORDER = ["es-MX", "es-US", "es-CO", "es-PE", "es-VE", "es-EC", "es-BO", "es-CR", "es-PA",
                  "es-GT", "es-HN", "es-SV", "es-NI"]

# Idiomas de narración: (id, nombre, idioma, acentos preferidos, acentos de respaldo)
# Los preferidos van primero; los de respaldo solo entran cuando ya no quedan voces libres.
VARIANTS = [
    ("es-419", "Español latinoamericano (neutro)", "es", _NEUTRAL_ORDER, []),
    ("es-MX", "Español de México", "es", ["es-MX"], _NEUTRAL_ORDER),
    ("es-CO", "Español de Colombia", "es", ["es-CO"], _NEUTRAL_ORDER),
    ("es-PE", "Español de Perú", "es", ["es-PE"], _NEUTRAL_ORDER),
    ("es-AR", "Español rioplatense (Argentina, Uruguay)", "es", ["es-AR", "es-UY", "es-PY"], []),
    ("es-CL", "Español de Chile", "es", ["es-CL"], []),
    ("es-ES", "Español de España", "es", ["es-ES"], []),
    ("es", "Español (todos los acentos)", "es", [], []),
    ("en-US", "Inglés de Estados Unidos", "en", ["en-US"], ["en-CA"]),
    ("en-GB", "Inglés británico", "en", ["en-GB"], ["en-IE", "en-AU"]),
    ("pt-BR", "Portugués de Brasil", "pt", ["pt-BR"], []),
    ("pt-PT", "Portugués de Portugal", "pt", ["pt-PT"], []),
    ("fr-FR", "Francés", "fr", ["fr-FR"], ["fr-CA", "fr-BE", "fr-CH"]),
    ("it-IT", "Italiano", "it", ["it-IT"], []),
    ("de-DE", "Alemán", "de", ["de-DE"], ["de-AT", "de-CH"]),
    ("ja-JP", "Japonés", "ja", ["ja-JP"], []),
    ("ko-KR", "Coreano", "ko", ["ko-KR"], []),
    ("zh-CN", "Chino mandarín", "zh", ["zh-CN"], ["zh-TW"]),
]
_VARIANT = {v[0]: v for v in VARIANTS}

# Las mejores voces primero (calidad y naturalidad al narrar)
RANK = {
    "Male": ES_MALE + ["en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural", "en-US-GuyNeural",
                       "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-RogerNeural", "en-GB-RyanNeural",
                       "en-GB-ThomasNeural", "pt-BR-AntonioNeural", "fr-FR-RemyMultilingualNeural",
                       "fr-FR-HenriNeural", "de-DE-FlorianMultilingualNeural", "it-IT-GiuseppeMultilingualNeural",
                       "it-IT-DiegoNeural", "ja-JP-KeitaNeural", "ko-KR-InJoonNeural", "zh-CN-YunxiNeural"],
    "Female": ES_FEMALE + ["en-US-AvaMultilingualNeural", "en-US-EmmaMultilingualNeural", "en-US-JennyNeural",
                           "en-US-AriaNeural", "en-US-MichelleNeural", "en-GB-SoniaNeural", "en-GB-LibbyNeural",
                           "pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural",
                           "fr-FR-VivienneMultilingualNeural", "fr-FR-DeniseNeural",
                           "de-DE-SeraphinaMultilingualNeural", "it-IT-ElsaNeural", "ja-JP-NanamiNeural",
                           "ko-KR-SunHiNeural", "zh-CN-XiaoxiaoNeural"],
}


def variant_info(vid):
    """(id, nombre, idioma, preferidos, respaldo). Acepta también un idioma suelto ('en')."""
    if vid in _VARIANT:
        return _VARIANT[vid]
    lang = (vid or "es").split("-")[0]
    return (lang, LANG_NAMES.get(lang, lang), lang, [], [])


def variants_for(voices):
    """Variantes que tienen voces disponibles, más un idioma genérico para el resto de idiomas."""
    locs = {v.locale for v in voices}
    langs = {v.lang for v in voices}
    out = [v for v in VARIANTS if (not v[3] and v[2] in langs) or any(l in locs for l in v[3])]
    known = {v[2] for v in out}
    for l in sorted(langs - known, key=lambda x: LANG_NAMES.get(x, x)):
        out.append(variant_info(l))
    return out


def variant_from_legacy(lang, neutral=True):
    if lang == "es":
        return "es-419" if neutral else "es"
    return {"en": "en-US", "pt": "pt-BR", "fr": "fr-FR", "it": "it-IT", "de": "de-DE",
            "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-CN"}.get(lang, lang)


def ordered_pool(voices, lang, gender, neutral=True):
    """Voces de un género, de mejor a peor. neutral puede ser True/False (antiguo) o un id de variante."""
    if isinstance(neutral, str):
        _, _, lang, pref, back = variant_info(neutral)
    else:
        pref, back = (_NEUTRAL_ORDER, []) if (lang == "es" and neutral) else ([], [])
    vs = [v for v in voices if v.lang == lang and v.gender == gender]
    if pref:
        order = {l: i for i, l in enumerate(list(pref) + list(back))}
        sel = [v for v in vs if v.locale in order]
        vs = sel or vs
    else:
        order = {}
    rank = {n: i for i, n in enumerate(RANK.get(gender, []))}
    npref = len(pref)
    vs.sort(key=lambda v: (order.get(v.locale, 999) >= npref if pref else False, v.multilingual and v.lang != "en",
                           order.get(v.locale, 999) if pref else 0, rank.get(v.name, 500), v.name))
    return vs


AGE_MOD = {"niño": (8, 4), "joven": (2, 1), "adulto": (0, 0), "anciano": (-4, -8)}  # (tono, velocidad)


def auto_cast(chars, voices, lang, narrator, existing=None, neutral=True):
    """
    chars: lista de brain.Character ordenada por importancia.
    Devuelve {nombre: {voice, pitch, rate, gender, age, auto}} conservando lo que ya existe.
    """
    existing = dict(existing or {})
    used = {narrator} | {c["voice"] for c in existing.values()}
    males = ordered_pool(voices, lang, "Male", neutral)
    females = ordered_pool(voices, lang, "Female", neutral)
    shifts = [0, 4, -4, 7, -7, 10]
    counters = {"M": 0, "F": 0}
    for ch in chars:
        if ch.name in existing:
            continue
        g = ch.gender
        pool = females if g == "F" else males
        if not pool:
            pool = males or females
        free = [v for v in pool if v.name not in used]
        if free:
            v, sh = free[0], 0
        else:
            i = counters[g]
            v = pool[i % len(pool)]
            sh = shifts[1 + (i // len(pool)) % (len(shifts) - 1)]
            if v.name == narrator and len(pool) > 1:
                v = pool[(i + 1) % len(pool)]
        counters[g] += 1
        used.add(v.name)
        ap, ar = AGE_MOD.get(ch.age, (0, 0))
        base = -3 if g == "M" else 0
        existing[ch.name] = {"voice": v.name, "pitch": base + sh + ap, "rate": ar,
                             "gender": g, "age": ch.age, "auto": True}
    return existing
