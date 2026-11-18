# -*- coding: utf-8 -*-
"""Modo estudio: fórmulas, unidades, citas y tablas para leer material universitario en voz alta.

- math_speech(): convierte fórmulas y unidades escritas en texto a español hablado
  («d²y/dx² + ω²y = 0» -> «la segunda derivada de y respecto de x, más omega al cuadrado por y, igual a 0»).
- omml_to_linear(): ecuaciones de Word (OMML) -> texto lineal que math_speech entiende.
- latex_to_linear(): LaTeX ($…$, \\frac, \\int…) -> texto lineal.
- table_lines(): tabla -> «Tabla 1. Fila 1: Voltaje: 220 V; Corriente: 5 A.».
- citas [12], (Autor, 2020) y la sección «Referencias».
Las mismas reglas están en brain.js (mathSpeech) e import.js para que la PC y el teléfono lean igual.
"""
import re
import unicodedata
import xml.etree.ElementTree as ET

# ----------------------------------------------------------------------------- citas y referencias
CITE_RE = re.compile(
    r"\s?\[\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*\]"                                     # [12] [3, 5] [4-7]
    r"|\s?\([A-ZÁÉÍÓÚÑ][\w'´\-]+(?:\s+(?:et\s+al\.?|y|e|&|and)\s*(?:[A-ZÁÉÍÓÚÑ][\w'´\-]+)?)?,?\s+"
    r"(?:\d{4}[a-z]?|s\.\s?f\.)(?:,\s*p+\.\s*\d+(?:[-–]\d+)?)?"
    r"(?:\s*;\s*[A-ZÁÉÍÓÚÑ][\w'´\-]+(?:\s+(?:et\s+al\.?|y|&)\s*(?:[A-ZÁÉÍÓÚÑ][\w'´\-]+)?)?,?\s+\d{4}[a-z]?)*\)")
REF_HEAD = re.compile(r"^\s*(?:\d+\.?\s*)?(?:referencias(?:\s+bibliogr[aá]ficas)?|bibliograf[ií]a|"
                      r"references|bibliography|fuentes\s+consultadas|webgraf[ií]a)\s*:?\s*$", re.I)


def strip_citations(t):
    return CITE_RE.sub("", t)


# ----------------------------------------------------------------------------- unidades
_UNITS = [
    ("m/s²", "metros por segundo al cuadrado", "metro por segundo al cuadrado"), ("m/s^2", "metros por segundo al cuadrado", None),
    ("km/h", "kilómetros por hora", "kilómetro por hora"), ("m/s", "metros por segundo", "metro por segundo"),
    ("rad/s", "radianes por segundo", "radián por segundo"), ("kg/m³", "kilogramos por metro cúbico", None),
    ("kg/m3", "kilogramos por metro cúbico", None), ("m/s2", "metros por segundo al cuadrado", None),
    ("W/m²", "vatios por metro cuadrado", None), ("W/m2", "vatios por metro cuadrado", None),
    ("kJ/kg", "kilojulios por kilogramo", None), ("J/kg", "julios por kilogramo", None), ("m³/s", "metros cúbicos por segundo", None),
    ("m3/s", "metros cúbicos por segundo", None), ("L/s", "litros por segundo", None), ("L/min", "litros por minuto", None),
    ("kWh/kg", "kilovatios hora por kilogramo", None), ("Wh/kg", "vatios hora por kilogramo", None),
    ("Wh/L", "vatios hora por litro", None), ("g/ml", "gramos por mililitro", None), ("g/cm³", "gramos por centímetro cúbico", None),
    ("g/cm3", "gramos por centímetro cúbico", None), ("kg/m 3", "kilogramos por metro cúbico", None),
    ("kg/m", "kilogramos por metro", None), ("ml", "mililitros", "mililitro"),
    ("kW·h", "kilovatios hora", "kilovatio hora"), ("Wh", "vatios hora", "vatio hora"), ("N·m", "newton metro", None), ("kWh", "kilovatios hora", "kilovatio hora"),
    ("GPa", "gigapascales", "gigapascal"), ("GW", "gigavatios", "gigavatio"), ("MVA", "megavoltamperios", None),
    ("mΩ", "miliohmios", "miliohmio"), ("μA", "microamperios", "microamperio"), ("µA", "microamperios", "microamperio"),
    ("μm", "micrómetros", "micrómetro"), ("µm", "micrómetros", "micrómetro"), ("nm", "nanómetros", "nanómetro"),
    ("hp", "caballos de fuerza", "caballo de fuerza"), ("HP", "caballos de fuerza", "caballo de fuerza"),
    ("kcal", "kilocalorías", "kilocaloría"), ("cal", "calorías", "caloría"), ("Btu", "BTU", "BTU"), ("lbf", "libras fuerza", None),
    ("lb", "libras", "libra"), ("ft", "pies", "pie"), ("kgf", "kilogramos fuerza", None), ("mol", "moles", "mol"),
    ("Wb", "webers", "weber"), ("dB", "decibelios", "decibelio"), ("lx", "lux", "lux"), ("rad", "radianes", "radián"),
    ("min", "minutos", "minuto"), ("m3", "metros cúbicos", "metro cúbico"), ("m2", "metros cuadrados", "metro cuadrado"),
    ("cm3", "centímetros cúbicos", None), ("cm2", "centímetros cuadrados", None), ("kVA", "kilovoltamperios", None),
    ("kVAR", "kilovoltamperios reactivos", None), ("VAR", "voltamperios reactivos", None), ("VA", "voltamperios", "voltamperio"),
    ("MW", "megavatios", "megavatio"), ("kW", "kilovatios", "kilovatio"), ("mW", "milivatios", "milivatio"), ("W", "vatios", "vatio"),
    ("kV", "kilovoltios", "kilovoltio"), ("mV", "milivoltios", "milivoltio"), ("V", "voltios", "voltio"),
    ("kA", "kiloamperios", "kiloamperio"), ("mA", "miliamperios", "miliamperio"), ("A", "amperios", "amperio"),
    ("MΩ", "megaohmios", "megaohmio"), ("kΩ", "kiloohmios", "kiloohmio"), ("Ω", "ohmios", "ohmio"),
    ("MHz", "megahercios", "megahercio"), ("kHz", "kilohercios", "kilohercio"), ("Hz", "hercios", "hercio"),
    ("μF", "microfaradios", "microfaradio"), ("µF", "microfaradios", "microfaradio"), ("nF", "nanofaradios", "nanofaradio"),
    ("pF", "picofaradios", "picofaradio"), ("mH", "milihenrios", "milihenrio"),
    ("kJ", "kilojulios", "kilojulio"), ("MJ", "megajulios", "megajulio"), ("J", "julios", "julio"),
    ("kN", "kilonewtons", "kilonewton"), ("N", "newtons", "newton"),
    ("MPa", "megapascales", "megapascal"), ("kPa", "kilopascales", "kilopascal"), ("Pa", "pascales", "pascal"),
    ("atm", "atmósferas", "atmósfera"), ("bar", "bares", "bar"), ("psi", "psi", "psi"),
    ("°C", "grados Celsius", "grado Celsius"), ("ºC", "grados Celsius", "grado Celsius"), ("°F", "grados Fahrenheit", "grado Fahrenheit"),
    ("K", "kelvin", "kelvin"), ("rpm", "revoluciones por minuto", "revolución por minuto"),
    ("m²", "metros cuadrados", "metro cuadrado"), ("m³", "metros cúbicos", "metro cúbico"),
    ("cm²", "centímetros cuadrados", None), ("cm³", "centímetros cúbicos", None), ("mm²", "milímetros cuadrados", None),
    ("kg", "kilogramos", "kilogramo"), ("km", "kilómetros", "kilómetro"), ("cm", "centímetros", "centímetro"),
    ("mm", "milímetros", "milímetro"), ("ms", "milisegundos", "milisegundo"), ("μs", "microsegundos", "microsegundo"),
    ("g", "gramos", "gramo"), ("m", "metros", "metro"), ("s", "segundos", "segundo"), ("L", "litros", "litro"),
    ("°", "grados", "grado"), ("º", "grados", "grado"),
]
_UNIT_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?(" + "|".join(re.escape(u) for u, _, _ in
                      sorted(_UNITS, key=lambda x: -len(x[0]))) + r")(?![\w²³/])")
_UNIT_MAP = {u: (pl, sg) for u, pl, sg in _UNITS}


def _unit(m):
    num, u = m.group(1), m.group(2)
    pl, sg = _UNIT_MAP[u]
    return f"{num} {sg if (num in ('1', '1,0', '1.0') and sg) else pl}"


# ----------------------------------------------------------------------------- símbolos
GREEK = {"α": "alfa", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "épsilon", "ϵ": "épsilon", "ζ": "zeta", "η": "eta",
         "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "µ": "mu", "ν": "nu", "ξ": "xi",
         "π": "pi", "ρ": "ro", "σ": "sigma", "τ": "tau", "υ": "ípsilon", "φ": "fi", "ϕ": "fi", "χ": "ji", "ψ": "psi",
         "ω": "omega", "Γ": "gamma mayúscula", "Δ": "delta", "Θ": "theta mayúscula", "Λ": "lambda mayúscula",
         "Ξ": "xi mayúscula", "Π": "pi mayúscula", "Σ": "sigma mayúscula", "Φ": "fi mayúscula", "Ψ": "psi mayúscula",
         "Ω": "omega mayúscula", "∆": "delta"}
SYMBOLS = [("≤", " menor o igual que "), ("≥", " mayor o igual que "), ("≠", " distinto de "), ("≈", " aproximadamente "),
           ("≅", " aproximadamente "), ("≡", " equivale a "), ("±", " más o menos "), ("∓", " menos o más "),
           ("×", " por "), ("÷", " entre "), ("⇒", " implica "), ("⇔", " si y solo si "), ("→", " tiende a "),
           ("∈", " pertenece a "), ("∉", " no pertenece a "), ("∀", " para todo "), ("∃", " existe "),
           ("∞", " infinito "), ("∇", " nabla "), ("∠", " ángulo "), ("⊥", " perpendicular a "), ("∥", " paralelo a "),
           ("∝", " proporcional a "), ("∑", " la sumatoria de "), ("∏", " el producto de "), ("∮", " la integral cerrada de "),
           ("∬", " la integral doble de "), ("∫", " la integral de "), ("∂", " parcial de "), ("√", " raíz de "),
           ("∛", " raíz cúbica de "), ("′′", " segunda "), ("″", " segunda "), ("′", " prima "), ("−", " menos "),
           ("∙", " por "), ("⋅", " por "),
           ("⊆", " subconjunto o igual a "), ("⊂", " subconjunto de "), ("⊇", " contiene o es igual a "), ("⊃", " contiene a "),
           ("∪", " unión "), ("∩", " intersección "), ("∅", " el conjunto vacío "), ("∄", " no existe "), ("¬", " no "),
           ("∧", " y "), ("∨", " o "), ("⊕", " o exclusivo "), ("⊗", " producto tensorial "), ("∘", " compuesta con "),
           ("≪", " mucho menor que "), ("≫", " mucho mayor que "), ("≜", " se define como "), ("≝", " se define como "),
           ("≃", " aproximadamente igual a "), ("∼", " del orden de "), ("≢", " no equivale a "), ("⟺", " si y solo si "),
           ("⟹", " implica "), ("⇐", " se deduce de "), ("↔", " equivale a "), ("↦", " se asigna a "),
           ("⇌", " en equilibrio con "), ("∡", " ángulo "), ("⟂", " perpendicular a "), ("‰", " por mil "),
           ("ℝ", " los reales "), ("ℕ", " los naturales "), ("ℤ", " los enteros "), ("ℚ", " los racionales "),
           ("ℂ", " los complejos "), ("ℒ", " la transformada de Laplace de "), ("ℱ", " la transformada de Fourier de "),
           ("ℏ", " h barra "), ("ℓ", " ele "), ("∭", " la integral triple de "), ("∯", " la integral de superficie cerrada de "),
           ("‖", " norma "), ("⌊", " el piso de "), ("⌋", " "), ("⌈", " el techo de "), ("⌉", " "), ("⟨", " "), ("⟩", " "),
           ("∗", " convolución ")]
SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ", "0123456789+-ni")
SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋ₙₓₐₑ", "0123456789+-nxae")
FUNCS = {"sen": "seno", "sin": "seno", "cos": "coseno", "tan": "tangente", "tg": "tangente", "cot": "cotangente",
         "sec": "secante", "csc": "cosecante", "cosec": "cosecante", "arcsen": "arcoseno", "arcsin": "arcoseno",
         "arccos": "arcocoseno", "arctan": "arcotangente", "arctg": "arcotangente", "senh": "seno hiperbólico",
         "sinh": "seno hiperbólico", "cosh": "coseno hiperbólico", "tanh": "tangente hiperbólica",
         "ln": "logaritmo natural", "log": "logaritmo", "exp": "exponencial", "coth": "cotangente hiperbólica",
         "arcsec": "arcosecante", "arccot": "arcocotangente", "det": "determinante", "sgn": "signo"}

_TRIGGER = re.compile(r"[\^_=∫∑∂√±×÷≤≥≠≈→∞⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ᵀαβγδεθλμπρσφωΔΩΣΠ′″−·⋅∙•|‖~!<>*/∠∆ℝℕℤℚℂℒℱℏ⊂⊆⊃∪∩∅∧∨¬∘≪≫≡≜∼⇒⇔↔∈∉∀∃∇∮∬∭∯\\\u0300-\u036f\u20d7]|"
                      r"\d\s?[a-zA-ZΩ°º%μµ]|\s\.\s|"
                      r"\b(?:sen|cos|ln|log|exp|tg|det)\b|\b[a-zA-Z]/[a-zA-Z]\b|\b\w+'")

MATH_MODES = ("auto", "on", "off")      # Automático (frase por frase), Siempre, Nunca

# señales de que una frase es una fórmula (modo Automático)
_SIG_STRONG = re.compile(r"[\w)\]}!′'²³|]\s*(?:=|:=|≤|≥|≠|≈|<=|>=|<|>)\s*[\w(√\-−∫∑∂|]|[∫∑∏∂∇√∮∬∭]|\^|\$[^$\n]*[\\^_]|"
                         r"\\(?:frac|int|sum|sqrt)|\b[dD]\w?\s*/\s*d\w\b|\blim\b")
_SIG_WEAK = re.compile(r"\d\s*[+\-−*/×÷·⋅]\s*\d|(?<![\w])[a-zA-Zα-ω]\s*[+\-−*/·⋅]\s*[a-zA-Zα-ω](?![\w])|"
                       r"\b(?:sen|cos|tan|tg|ln|log|exp|det|f|g|h)\s*\(|[αβγδεθλμπρσφωΔΩΣΠ∆]|[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]|"
                       r"\b[a-zA-Z]\s?'{1,2}(?![\w'])|\|\w{1,6}\|")


def math_score(t):
    t = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", "", t)          # las fechas no son fracciones
    s = 2 * len(_SIG_STRONG.findall(t)) + len(_SIG_WEAK.findall(t))
    letters = sum(ch.isalpha() for ch in t)
    syms = sum(1 for ch in t if ch in "=+-−*/^()[]{}|<>·⋅×÷√∫∑∂±≤≥≠≈")
    if letters and syms / (letters + syms) > 0.18:
        s += 1
    return s


def is_math(t):
    return math_score(t) >= 2


def separators(t):
    """«lugar · Europa · Lima» y «•» de listas son separadores, no multiplicaciones: se leen como pausa."""
    t = re.sub(r"(?m)^[ \t]*[·•∙▪◦‣][ \t]*", "", t)
    t = re.sub(r"(?<=[^\W\d_]{2})[ \t]+[·•∙⋅|][ \t]+(?=[^\W\d_]{2})", ", ", t)
    t = re.sub(r"(?<=[^\W\d_]{2})[ \t]+[·•∙][ \t]+(?=\d)|(?<=\d)[ \t]+[·•∙][ \t]+(?=[^\W\d_]{2})", ", ", t)
    return re.sub(r"(?<=[^\W\d_])[ \t]+\.[ \t]+(?=[^\W\d_])", ". ", t)      # «lugar . Europa» -> «lugar. Europa»


def _marks(t):
    """Puntos, barras, sombreros y flechas encima de una letra: ẋ ẍ ṁ Q̇ x̄ x̂ v⃗ Aᵀ."""
    if not re.search(r"[\u0300-\u036f\u20d7\u1e00-\u1effᵀ]", t):
        return t
    d = unicodedata.normalize("NFD", t)
    d = re.sub(r"([A-Za-zα-ωΑ-Ω])\u20d7", r" el vector \1 ", d)
    d = re.sub(r"([A-Za-zα-ωΑ-Ω])\u0307", r" \1 punto ", d)
    d = re.sub(r"([b-df-hj-np-tv-zB-DF-HJ-NP-TV-Zα-ωΑ-Ω])\u0308", r" \1 dos puntos ", d)
    d = re.sub(r"([A-Za-zα-ωΑ-Ω])\u0304", r" \1 barra ", d)
    d = re.sub(r"([b-df-hj-np-tv-zB-DF-HJ-NP-TV-Zα-ωΑ-Ω])\u0302", r" \1 sombrero ", d)
    d = re.sub(r"(\w)ᵀ", r"\1 transpuesta ", d)
    return unicodedata.normalize("NFC", d)


def _pow(exp):
    e = exp.strip().strip("()").strip().replace("−", "-")
    if e == "2":
        return " al cuadrado"
    if e == "3":
        return " al cubo"
    if e == "-1":
        return " a la menos uno"
    if e == "T":
        return " transpuesta"
    if re.fullmatch(r"\d+", e):
        return f" a la {e}"
    if re.fullmatch(r"-\d+", e):
        return f" a la menos {e[1:]}"
    if re.fullmatch(r"[a-zA-Z]", e):
        return f" a la {e}"
    e = re.sub(r"^-\s*", "menos ", e)
    return f" elevado a {e},"


_FUNC_RE = "|".join(sorted(FUNCS, key=len, reverse=True))


def math_speech(t, mode="auto"):
    """Fórmulas y unidades escritas -> español hablado. El texto sin fórmulas sale igual.
    mode: «auto» = solo las frases que parecen fórmulas usan las reglas dudosas (·, *, /, ', -, =);
    «on» = todo el texto es matemático; «off» = no se toca nada (solo los separadores)."""
    if not t:
        return t
    if mode is True:
        mode = "auto"
    elif mode is False or mode not in MATH_MODES:
        mode = "off" if mode is False else "auto"
    if not _TRIGGER.search(t):
        return t
    t = separators(t)
    if mode == "off":
        return t
    # LaTeX dentro del texto: $…$, \( … \), \[ … \]
    t = re.sub(r"\$\$(.+?)\$\$|\$(?=[^$\n]*[\\^_])([^$\n]+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]",
               lambda m: latex_to_linear(next(g for g in m.groups() if g is not None)), t, flags=re.S)
    t = t.translate({ord("\u2009"): " ", ord("\u202f"): " "})
    t = _marks(t)
    full = mode == "on" or is_math(t)
    # fasores: 10∠30° -> 10 con ángulo de 30 grados
    t = re.sub(r"∠\s*([-−]?\d[\d.,]*)\s*[°º]?", r" con ángulo de \1 grados ", t)
    if full:
        t = re.sub(r"\b(\d+(?:[.,]\d+)?)[eE]([-+−]?)(\d+)\b",
                   lambda m: f"{m.group(1)} por 10 a la {'menos ' if m.group(2) in '-−' and m.group(2) else ''}{m.group(3)}", t)
        t = re.sub(r"\bP\(\s*([^|()]{1,20}?)\s*\|\s*([^|()]{1,20}?)\s*\)", r" la probabilidad de \1 dado \2 ", t)
        if "probabilidad de" in t or re.search(r"[∩∪]", t):     # P(B) es probabilidad solo junto a P(A|B), ∩ o ∪
            t = re.sub(r"\bP\(\s*([A-Z](?:\s*[∩∪]\s*[A-Z])*)\s*\)", r" la probabilidad de \1 ", t)
        t = re.sub(r"[{}]", " ", t)
        t = re.sub(r"‖([^‖]{1,30})‖", r" la norma de \1 ", t)
        t = re.sub(r"(?<![|\w])\|\s*([^|\s][^|]{0,24}?)\s*\|(?![|\w])", r" el valor absoluto de \1 ", t)
        t = re.sub(r"(?<=[\w)])\s*\|\|\s*(?=[\w(])", " en paralelo con ", t)
        t = re.sub(r"(?<=[\w)])!(?![=\w])", " factorial", t)
        t = t.replace(":=", " se define como ").replace("<=", "≤").replace(">=", "≥").replace("!=", "≠").replace("==", "=")
        t = re.sub(r"(?<=[\w)])\s*=>\s*", " implica ", t)
        t = re.sub(r"(?<![\w~])~\s*(?=\d)", " aproximadamente ", t)
    eq = "=" in t        # en una fórmula, «4L» o «2A» pegados son variables (4 por L), no litros ni amperios
    t = _UNIT_RE.sub(lambda m: m.group(0) if (eq and len(m.group(2)) == 1 and m.group(0)[len(m.group(1))] != " ")
                     else _unit(m), t)                           # 220 V -> 220 voltios (antes de tocar / y ²)
    # superíndices y subíndices de Unicode -> ^ y _
    # (ω²y -> ω^2·y: la letra pegada después de la potencia es una multiplicación)
    t = re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ]+)([a-zA-Z(])?",
               lambda m: "^" + m.group(1).translate(SUP) + ("·" + m.group(2) if m.group(2) else " "), t)
    t = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋ₙₓₐₑ]+", lambda m: "_" + m.group(0).translate(SUB) + " ", t)
    t = re.sub(r"(\^\([^()]*\))(?=[a-zA-Zα-ωΑ-Ω(])", r"\1·", t)          # «ω^(2)y» de Word -> «ω^(2)·y»
    # derivadas: d²y/dx², dy/dx, d/dx, ∂u/∂x, y'' y'  (también «(d^2y)/(dx^2)» que sale de LaTeX o Word)
    t = re.sub(r"\(\s*(d(?:\^\(?2\)?)?[\s·]?\w)\s*\)\s*/\s*\(\s*(d\w(?:\^\(?2\)?)?)\s*\)", r"\1/\2", t)
    t = re.sub(r"\bd\^\((\d)\)", r"d^\1", t)
    t = re.sub(r"\bd\^?2[\s·]?(\w)\s*/\s*d(\w)\^?\(?2\)?(?!\w)\s*", r" la segunda derivada de \1 respecto de \2 ", t)
    t = re.sub(r"\bd(\w)\s*/\s*d(\w)\b", r" la derivada de \1 respecto de \2 ", t)
    t = re.sub(r"\bd\s*/\s*d(\w)\b", r" la derivada respecto de \1 de ", t)
    t = re.sub(r"∂\^?2\s?(\w)\s*/\s*∂(\w)\^?2", r" la segunda derivada parcial de \1 respecto de \2 ", t)
    t = re.sub(r"∂(\w)\s*/\s*∂(\w)", r" la derivada parcial de \1 respecto de \2 ", t)
    if full:
        t = re.sub(r"(?<![a-zA-Z'])([a-zA-Z])\s?''(?!\w)", r"\1 segunda ", t)
        t = re.sub(r"(?<![a-zA-Z'])([a-zA-Z])\s?'(?![\w'])", r"\1 prima ", t)
    if full and "=" in t:             # fórmulas de PDF «aplastadas»: «d2x dt2», «dx dt», «coswt», «lnt»
        t = re.sub(r"\bd2\s?([a-zA-Z])\s+d([a-zA-Z])\s?2\b", r" la segunda derivada de \1 respecto de \2 ", t)
        t = re.sub(r"(?<![\w/])d([a-zA-Z])\s+d([a-zA-Z])\b(?!\s*/)", r" la derivada de \1 respecto de \2 ", t)
        t = re.sub(r"\b(cos|sen|tan|tg)(?=[b-df-hj-np-tv-zw]{1,3}\b)", r"\1 ", t)
        t = re.sub(r"\bln(?=[a-z])", "ln ", t)
    t = t.replace("∴", " por lo tanto ").replace("∵", " porque ")
    if "∫" in t:                      # «dt» al final de una integral (antes de escribir «de» en español)
        t = re.sub(r"(?<=[\s)])d([a-zA-Z])(?=\s*(?:$|[,.;)=]))", r" diferencial de \1", t)
    # límites, integrales y sumatorias con límites
    t = re.sub(r"\blim\s*_\s*\(?\s*([a-zA-Z])\s*(?:→|->|tiende a)\s*([^)\s]+)\s*\)?",
               r" el límite cuando \1 tiende a \2 de ", t)
    t = re.sub(r"([∫∑∏])\s*_\s*\(?([^()^\s]+)\)?\s*\^\s*\(?([^()\s]+)\)?",
               lambda m: {"∫": " la integral desde ", "∑": " la sumatoria desde ", "∏": " el producto desde "}[m.group(1)]
               + m.group(2) + " hasta " + m.group(3) + " de ", t)
    # funciones: con paréntesis siempre; «sen x» sin paréntesis solo en fórmulas (si no, «tan claro» sería «tangente»)
    t = re.sub(r"\b(" + _FUNC_RE + r")\b" + (r"(?:\s*\(|\s+(?=[a-zA-Zα-ω0-9]))" if full else r"\s*\("),
               lambda m: f" {FUNCS[m.group(1)]} de " + ("(" if m.group(0).rstrip().endswith("(") else ""), t)
    # potencias y subíndices
    t = re.sub(r"\^\s*(\([^()]{1,40}\)|[-−]?[\w.]+)", lambda m: _pow(m.group(1)), t)
    t = re.sub(r"(?<=\w)_\s*(\([^()]{1,30}\)|\w+)", lambda m: " sub " + m.group(1).strip("()") + " ", t)
    # raíces y fracciones
    t = re.sub(r"√\s*\[(\w+)\]\s*", r" raíz de índice \1 de ", t)
    if full:
        # «1/√(LC)»: antes quedaba «1/ raíz de (LC)». La raíz se lee entera y la barra delante es «entre»;
        # dentro de la raíz, 2-3 letras sueltas pegadas son un producto (LC = L por C)
        def _root(m):
            inner = m.group(1).strip()
            if re.fullmatch(r"[A-Za-zα-ω]{2,3}", inner):
                inner = " por ".join(inner)
            return " la raíz de " + inner + " "
        t = re.sub(r"(?<=[\w)])\s*/\s*(?=√)", " entre ", t)
        t = re.sub(r"(?<=[\w)])(?=√)", "·", t)                        # «2π√(LC)»: pegado es multiplicación
        t = re.sub(r"√\s*\(([^()]{1,40})\)", _root, t)
    if not full:                      # en prosa solo las fracciones cortas y claras: «T/µ», «L2/12», «1/2» (no «y/o», ni fechas)
        t = re.sub(r"(?<![\w/])([a-zA-Zα-ωµ]\w?|\d{1,2})\s*/\s*([a-zA-Zα-ωµ]\w?|\d{1,2})(?![\w/])",
                   lambda m: m.group(0) if (m.group(1).lower(), m.group(2).lower()) in (("y", "o"), ("o", "y")) or
                   (len(m.group(1)) > 1 and len(m.group(2)) > 1 and m.group(1).isupper() and m.group(2).isupper()) else
                   f"{m.group(1)} sobre {m.group(2)}", t)              # «AC/DC» y «DC/DC» son siglas
    t = re.sub(r"\(([^()]{1,60})\)\s*/\s*\(([^()]{1,60})\)", r" la fracción \1, entre \2, ", t)
    t = re.sub(r"\(([^()]{1,40})\)\s*/\s*([\w.,]{1,6})(?![\w/])", r" \1 sobre \2", t)
    if full:
        t = re.sub(r"(?<![\w/])([\w.,]{1,6})\s*/\s*([\w.,]{1,6})(?![\w/])",
                   lambda m: m.group(0) if (m.group(1).lower(), m.group(2).lower()) in (("y", "o"), ("o", "y")) or
                   (m.group(1).isdigit() and m.group(2).isdigit() and len(m.group(2)) > 2) else
                   f"{m.group(1)} sobre {m.group(2)}", t)
        t = re.sub(r"(?<=[\w)])(?<!\d)\s*/\s*(?=[\w(])|(?<=\d)\s*/\s*(?=[^\W\d_]|\()", " entre ", t)   # «1/(s+1)», «… / P(B)»
    # símbolos y letras griegas («Ω» suelto después de un valor es la unidad: ohmios)
    t = re.sub(r"(?<=[\d\s])Ω(?=\s|$|[.,;)])", " ohmios", t)
    for a, b in SYMBOLS:
        if a in t:
            t = t.replace(a, b)
    t = "".join(" " + GREEK[c] + " " if c in GREEK else c for c in t)
    # operadores entre operandos
    t = re.sub(r"(?<=[\w)])\s*[·⋅∙]\s*(?=[\w(])", " por ", t)
    t = re.sub(r"(?<=[\w)\]'])\s*\+\s*(?=[\w(√])", " más ", t)
    if full:
        t = re.sub(r"(?<=[\w)\]])\s*=\s*(?=[\w(√\-−\s])", " igual a ", t)
        t = re.sub(r"(?<=[\w)])\s+-\s+(?=[\w(])|(?<=[\d)])\s*-\s*(?=[\d(])(?!\d{2,}\b-)|(?<=\b[a-zA-Z])-(?=\d\b)",
                   " menos ", t)
        t = re.sub(r"(?<=[\w)])\s*\*\s*(?=[\w(])", " por ", t)
        t = re.sub(r"(?<=\w)\s+<\s+(?=\w)", " menor que ", t)
        t = re.sub(r"(?<=\w)\s+>\s+(?=\w)", " mayor que ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return t.strip()


# ----------------------------------------------------------------------------- LaTeX -> lineal
_LATEX_CMDS = {r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\epsilon": "ε", r"\varepsilon": "ε",
               r"\zeta": "ζ", r"\eta": "η", r"\theta": "θ", r"\vartheta": "θ", r"\lambda": "λ", r"\mu": "μ",
               r"\nu": "ν", r"\xi": "ξ", r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ", r"\phi": "φ",
               r"\varphi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω", r"\Gamma": "Γ", r"\Delta": "Δ",
               r"\Theta": "Θ", r"\Lambda": "Λ", r"\Pi": "Π", r"\Sigma": "Σ", r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
               r"\cdot": "·", r"\times": "×", r"\div": "÷", r"\pm": "±", r"\mp": "∓", r"\leq": "≤", r"\le": "≤",
               r"\geq": "≥", r"\ge": "≥", r"\neq": "≠", r"\ne": "≠", r"\approx": "≈", r"\equiv": "≡", r"\infty": "∞",
               r"\to": "→", r"\rightarrow": "→", r"\Rightarrow": "⇒", r"\iff": "⇔", r"\Leftrightarrow": "⇔",
               r"\partial": "∂", r"\nabla": "∇", r"\int": "∫", r"\iint": "∬", r"\oint": "∮", r"\sum": "∑",
               r"\prod": "∏", r"\in": "∈", r"\forall": "∀", r"\exists": "∃", r"\angle": "∠", r"\perp": "⊥",
               r"\propto": "∝", r"\degree": "°", r"\circ": "°", r"\lim": "lim", r"\sin": "sen", r"\cos": "cos",
               r"\tan": "tan", r"\ln": "ln", r"\log": "log", r"\exp": "exp", r"\sinh": "senh", r"\cosh": "cosh",
               r"\tanh": "tanh", r"\Omega": "Ω", r"\ldots": "…", r"\cdots": "…", r"\dots": "…"}


def _brace(s, i):
    """Devuelve (contenido, fin) del grupo {…} que empieza en s[i] == '{'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def latex_to_linear(s):
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\s*([()\[\]|.]|\\\{|\\\})", lambda m: m.group(1).strip(".").lstrip("\\"), s)
    s = re.sub(r"\\(?:mathrm|mathbf|mathit|text|textrm|operatorname|boldsymbol|vec|hat|bar|overline)\s*", "", s)
    s = re.sub(r"\\[,;:! ]|\\quad|\\qquad|~", " ", s)
    out, i = [], 0
    while i < len(s):
        if s.startswith(("\\frac", "\\dfrac", "\\tfrac"), i):
            i = s.index("frac", i) + 4
            while i < len(s) and s[i] == " ":
                i += 1
            a, i = _brace(s, i) if i < len(s) and s[i] == "{" else (s[i:i + 1], i + 1)
            while i < len(s) and s[i] == " ":
                i += 1
            b, i = _brace(s, i) if i < len(s) and s[i] == "{" else (s[i:i + 1], i + 1)
            a, b = latex_to_linear(a), latex_to_linear(b)
            out.append(f"{a}/{b}" if re.fullmatch(r"\w{1,4}", a) and re.fullmatch(r"\w{1,4}", b) else f"({a})/({b})")
            continue
        if s.startswith("\\sqrt", i):
            i += 5
            idx = ""
            if i < len(s) and s[i] == "[":
                j = s.index("]", i)
                idx, i = s[i + 1:j], j + 1
            a, i = _brace(s, i) if i < len(s) and s[i] == "{" else (s[i:i + 1], i + 1)
            out.append(("√[" + idx + "]" if idx else "√") + "(" + latex_to_linear(a) + ")")
            continue
        if s[i] in "^_" and i + 1 < len(s) and s[i + 1] == "{":
            a, j = _brace(s, i + 1)
            out.append(s[i] + "(" + latex_to_linear(a) + ")")
            i = j
            continue
        if s[i] == "\\":
            m = re.match(r"\\[A-Za-z]+", s[i:])
            if m:
                cmd = m.group(0)
                out.append(_LATEX_CMDS.get(cmd, "") + (" " if cmd in _LATEX_CMDS and cmd[1:].isalpha() else ""))
                i += len(cmd)
                continue
        if s[i] in "{}":
            i += 1
            continue
        out.append(s[i])
        i += 1
    r = re.sub(r"[ ]{2,}", " ", "".join(out)).strip()
    r = re.sub(r"\s+\^", "^", r)
    # «ω^2 y» -> «ω^2·y» (en LaTeX el espacio después de una potencia es multiplicación)
    return re.sub(r"(\^(?:\([^()]*\)|\w))\s+(?=[a-zA-Zα-ωΑ-Ω(])", r"\1·", r)


def latex_document_text(src):
    """Archivo .tex completo -> texto para leer (solo el cuerpo, sin preámbulo ni comandos de formato)."""
    m = re.search(r"\\begin\{document\}(.*?)(?:\\end\{document\}|$)", src, re.S)
    body = m.group(1) if m else src
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\\(?:chapter|section|subsection|subsubsection)\*?\{([^}]*)\}", r"\n\n\1\n\n", body)
    body = re.sub(r"\\begin\{(?:equation|align|gather|multline|displaymath)\*?\}(.*?)\\end\{(?:equation|align|gather|"
                  r"multline|displaymath)\*?\}", lambda mm: "\n" + latex_to_linear(mm.group(1).replace("&", "")
                                                                                      .replace("\\\\", " ; ")) + "\n",
                  body, flags=re.S)
    body = re.sub(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\[(.+?)\\\]|\\\((.+?)\\\)",
                  lambda mm: latex_to_linear(next(g for g in mm.groups() if g is not None)), body, flags=re.S)
    body = re.sub(r"\\(?:textbf|textit|emph|underline)\{([^}]*)\}", r"\1", body)
    body = re.sub(r"\\item\s*", "\n- ", body)
    body = re.sub(r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?(?:\{[^}]*\})?", "\n", body)
    body = re.sub(r"\\(?:label|ref|cite|includegraphics|caption)(?:\[[^\]]*\])?\{[^}]*\}", "", body)
    body = re.sub(r"\\\\|\\newline|\\par\b", "\n", body)
    body = re.sub(r"\\[A-Za-z]+\*?", "", body)
    return re.sub(r"[{}]", "", body)


# ----------------------------------------------------------------------------- ecuaciones de Word (OMML)
_NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _ln(e):
    return e.tag.split("}")[-1]


def _kids(e, name):
    return [c for c in e if _ln(c) == name]


def _first(e, name):
    k = _kids(e, name)
    return k[0] if k else None


def _attr(e, name):
    if e is None:
        return None
    for k, v in e.attrib.items():
        if k.split("}")[-1] == "val":
            return v
    return None


def _wrap(x):
    x = x.strip()
    if len(x) <= 1 or re.fullmatch(r"\w+|\(.*\)", x):
        return x
    return "(" + x + ")"


def _om(e):
    n = _ln(e)
    if n in ("t",):
        return e.text or ""
    if n == "r":
        return "".join(_om(c) for c in e if _ln(c) == "t")
    if n == "f":
        a, b = _om(_first(e, "num")), _om(_first(e, "den"))
        if re.fullmatch(r"\w{1,4}", a.strip()) and re.fullmatch(r"\w{1,4}", b.strip()):
            return f"{a.strip()}/{b.strip()}"
        return f"({a.strip()})/({b.strip()})"
    if n == "sSup":
        return _om(_first(e, "e")) + "^(" + _om(_first(e, "sup")).strip() + ")"
    if n == "sSub":
        return _om(_first(e, "e")) + "_" + _wrap(_om(_first(e, "sub")))
    if n == "sSubSup":
        return _om(_first(e, "e")) + "_" + _wrap(_om(_first(e, "sub"))) + "^" + _wrap(_om(_first(e, "sup")))
    if n == "rad":
        deg = _om(_first(e, "deg")).strip() if _first(e, "deg") is not None else ""
        body = _om(_first(e, "e"))
        if deg in ("", "2"):
            return f"√({body})"
        if deg == "3":
            return f"∛({body})"
        return f"√[{deg}]({body})"
    if n == "nary":
        pr = _first(e, "naryPr")
        ch = _attr(_first(pr, "chr"), "val") if pr is not None else None
        ch = ch or "∫"
        sub = _om(_first(e, "sub")) if _first(e, "sub") is not None else ""
        sup = _om(_first(e, "sup")) if _first(e, "sup") is not None else ""
        lim = (f"_({sub})" if sub.strip() else "") + (f"^({sup})" if sup.strip() else "")
        return f"{ch}{lim} {_om(_first(e, 'e'))}"
    if n == "d":
        pr = _first(e, "dPr")
        beg = _attr(_first(pr, "begChr"), "val") if pr is not None else None
        end = _attr(_first(pr, "endChr"), "val") if pr is not None else None
        sep = _attr(_first(pr, "sepChr"), "val") if pr is not None else None
        beg = "(" if beg is None else beg
        end = ")" if end is None else end
        return beg + (sep or ", ").join(_om(c) for c in _kids(e, "e")) + end
    if n == "func":
        return _om(_first(e, "fName")).strip() + "(" + _om(_first(e, "e")) + ")"
    if n == "limLow":
        return _om(_first(e, "e")).strip() + "_(" + _om(_first(e, "lim")) + ") "
    if n == "limUpp":
        return _om(_first(e, "e")).strip() + "^(" + _om(_first(e, "lim")) + ") "
    if n == "m":
        rows = [", ".join(_om(c) for c in _kids(r, "e")) for r in _kids(e, "mr")]
        return "matriz [" + "; ".join(rows) + "]"
    if n == "eqArr":
        return " ; ".join(_om(c) for c in _kids(e, "e"))
    if n in ("rPr", "fPr", "sSupPr", "sSubPr", "radPr", "naryPr", "dPr", "funcPr", "ctrlPr", "accPr", "barPr",
             "limLowPr", "mPr", "eqArrPr", "boxPr", "groupChrPr", "sSubSupPr", "oMathParaPr", "limUppPr", "mcs"):
        return ""
    if n == "acc":
        ch = _attr(_first(_first(e, "accPr"), "chr"), "val") if _first(e, "accPr") is not None else None
        base = _om(_first(e, "e"))
        return base + (" punto" if ch in ("̇", "˙") else "")
    return "".join(_om(c) for c in e)


def omml_to_linear(fragment):
    """Fragmento <m:oMath>…</m:oMath> (o oMathPara) de un DOCX -> texto lineal («d²y/dx²», «√(x)», «∫_(0)^(t)»…)."""
    frag = re.sub(r"<(/?)(?!m:|w:)[A-Za-z0-9]+:[^>]*>", "", fragment)              # etiquetas de otros espacios
    frag = re.sub(r"\s(?!m:val|w:val)[A-Za-z0-9]+:[A-Za-z0-9]+=\"[^\"]*\"", "", frag)  # atributos extra
    try:
        root = ET.fromstring(f'<x xmlns:m="{_NS_M}" xmlns:w="{_NS_W}">{frag}</x>')
    except ET.ParseError:
        return " ".join(re.findall(r"<m:t[^>]*>([^<]*)</m:t>", fragment))
    import html as _html
    return _html.unescape(re.sub(r"[ ]{2,}", " ", _om(root)).strip())


# ----------------------------------------------------------------------------- tablas
def table_lines(rows, n):
    """[[celda…]…] -> líneas «Tabla n. Fila 1: Encabezado: valor; …». La primera fila se usa como encabezado
    si parece texto (no números)."""
    rows = [[re.sub(r"\s+", " ", c).strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    head = rows[0]
    use_head = len(rows) > 1 and len(head) > 1 and sum(1 for c in head if c and not re.fullmatch(r"[\d.,\s%-]+", c)) \
        >= max(1, len(head) // 2)
    out = [f"Tabla {n}" + (": " + ", ".join(c for c in head if c) if use_head else "") + "."]
    body = rows[1:] if use_head else rows
    # si la primera columna es texto (Voltaje, Corriente…) es el nombre de cada fila
    label = use_head and all(r and r[0] and not re.fullmatch(r"[\d.,\s%-]+", r[0]) for r in body)
    for i, r in enumerate(body, 1):
        if use_head:
            pairs = list(zip(head, r))[1 if label else 0:]
            cells = [f"{h}: {c}" if h else c for h, c in pairs if c] + [c for c in r[len(head):] if c]
        else:
            cells = [c for c in r if c]
        out.append(f"Fila {i}" + (f", {r[0]}" if label else "") + ": " + "; ".join(cells) + ".")
    out.append(f"Fin de la tabla {n}.")
    return out
