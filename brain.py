# -*- coding: utf-8 -*-
"""
Kuon IA: el "cerebro" local del estudio.

Lee la novela entera y decide, sin internet:
  - qué fragmentos son narración y cuáles diálogo,
  - quién dice cada diálogo (inciso "dijo Eren", acción previa "Eren apretó los puños.",
    párrafo anterior, pronombres, alternancia de una conversación y vocativos),
  - el género y la edad aproximada de cada personaje a partir del contexto,
  - la emoción de cada línea (grito, susurro, pensamiento, llanto, risa...)
  - y el ánimo de cada pasaje de narración (acción, tensión, tristeza).
"""
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from docs import CHAPTER_RE, BREAK_RE, DICT_F, DICT_M, DICT_COMMON, DICT_SPEECH, plain

# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------

VERB_STEMS = [
    "dij", "dic", "dec", "respond", "pregunt", "grit", "susurr", "murmur", "exclam", "contest",
    "añad", "replic", "insist", "interrump", "continu", "prosigu", "explic", "orden", "suplic",
    "rog", "bram", "rugi", "rug", "gru", "solt", "anunci", "declar", "afirm", "admit", "confes",
    "sentenci", "musit", "balbuc", "tartamud", "suspir", "advirt", "coment", "observ", "agreg",
    "inquir", "espet", "chill", "sollo", "jade", "vocifer", "mascull", "repus", "reclam",
    "protest", "pens", "reflexion", "bromeó", "bromea", "aull", "cuchiche", "titube", "vacil",
    "gimi", "gim", "llam", "intervin", "concluy", "record", "aclar", "propus", "sugir", "ret",
    "amenaz", "jur", "prometi", "rió", "ri", "sonri", "carcaj", "canturre", "corrig",
    "said", "ask", "repl", "shout", "whisper", "yell", "mutter", "answer", "cri",
]
SPEECH_VERB = (
    r"(?:dijo|dije|digo|dice|decía|"
    r"(?:respond|pregunt|grit|susurr|murmur|exclam|contest|añad|replic|insist|interrump|"
    r"continu|prosigu|sigui|explic|orden|suplic|rog|bram|rug|gruñ|solt|anunci|declar|afirm|admit|"
    r"confes|sentenci|musit|balbuc|tartamud|suspir|advirt|coment|observ|agreg|inquir|espet|"
    r"chill|sollo[zc]|jade|vocifer|mascull|repus|reclam|protest|pens|reflexion|brome|aull|"
    r"cuchiche|titube|vacil|gim|llam|intervin|concluy|record|aclar|propus|sugir|ret|amenaz|"
    r"jur|promet|ri|sonr|carcaj|canturre|corrig|habl|repit|repet|quej|mint|burl|resopl|sise|farfull|lament|"
    r"pidió|insinu|ironiz|confirm|inform|decret|elogi|salud|termin|empez|asinti|estall|said|ask|repl|shout|whisper|yell|mutter|answer|cri)"
    r"[a-záéíóúñ]{0,6})"
)
if DICT_SPEECH:      # formas sueltas del diccionario de verbos de habla ("rezongó", "increpó", "maldijo")
    SPEECH_VERB = r"(?:" + "|".join(sorted(DICT_SPEECH, key=lambda x: (-len(x), x))) + r"|" + SPEECH_VERB[3:]

RE_SPEECH = re.compile(r"\b" + SPEECH_VERB + r"\b", re.IGNORECASE)

NAME_TOKEN = r"[A-ZÁÉÍÓÚÑÜ][\wáéíóúñüāēīōū'’\-]*"
RE_CAP = re.compile(NAME_TOKEN)

STOP = set("""
El La Los Las Lo Le Les Un Una Unos Unas Y E O U Pero Entonces Luego Después Antes Mientras
Cuando Aunque Sin Con Por Para Así Ya No Sí Si Eso Esto Esta Este Estos Estas Aquel Aquella Ese
Esa Él Ella Ellos Ellas Yo Tú Vos Nosotros Nosotras Usted Ustedes Alguien Nadie Todos Todas Todo
Nada Algo Se Me Te Nos Su Sus Mi Mis Tu Tus De Del En A Al Que Qué Quien Quién Quienes Como Cómo
Cuál Cual Cuánto Dónde Donde Cuándo Porque Dios Señor Señora Capítulo Escena Parte Acto Prólogo
Epílogo The He She They It And Then But I You We Finalmente Incluso Solo Sólo También Tampoco
Ahora Allí Aquí Ahí Hoy Mañana Ayer Otro Otra Otros Otras Cada Hasta Desde Sobre Tras Entre Hacia
Contra Durante Mediante Según Bajo Ante Bien Mal Muy Más Menos Tan Tanto Quizás Quizá Tal Vez
Primero Segundo Tercero Uno Dos Tres Cuatro Cinco Diez Cien Mil Oh Ah Eh Ay Hmm Mmm Bueno Vale
Claro Gracias Hola Adiós Sí Basta Mira Mire Oye Oiga Vamos Ven Espera Calla Silencio Nunca Siempre
Jamás Aún Todavía Además Sin Embargo Pues Lunes Martes Miércoles Jueves Viernes Sábado Domingo
Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre Por Fin
Era Fue Había Hay Está Estaba Tenía Iba Podía Debía Sabía Quería Parecía Seguía Volvió Sintió
Miró Abrió Cerró Tomó Dio Hizo Puso Dejó Llegó Salió Entró Corrió Cayó Vio Oyó Notó Pensó Dijo
""".split())
# Personajes sin nombre que hablan ("—dijo el anciano"): se tratan como un personaje más
ROLE_NOUNS = set("""
rey reina príncipe princesa emperador emperatriz capitán capitana general comandante guardia soldado
mago maga bruja brujo hechicero hechicera desconocido desconocida extraño extraña encapuchado encapuchada
doctor doctora maestro maestra profesor profesora sacerdote sacerdotisa monje monja caballero sirviente
sirvienta mayordomo criada posadero posadera tabernero tabernera mercader vendedor vendedora juez pirata
chamán duque duquesa conde condesa barón baronesa herrero herrera cazador cazadora guerrero guerrera
asesino asesina ladrón ladrona mayor menor
""".split())
# ("el hombre", "la mujer", "el demonio" casi siempre son un personaje con nombre: no se tratan como rol)

ROLE_FEM_ART = "la"
ROLE_PHRASE = r"(?:[Ee]l|[Ll]a)\s+(?:" + "|".join(sorted(ROLE_NOUNS, key=lambda w: (-len(w), w))) + r")\b(?!\s+(?:de|del)\b)"
ROLE_RE = re.compile(ROLE_PHRASE)


def role_name(phrase):
    """'el rey' / 'La reina' -> 'El rey' / 'La reina'."""
    p = phrase.split()
    return p[0].capitalize() + " " + p[1].lower()


# Personajes sin nombre "genéricos" (la chica, la silueta, la voz): solo cuentan como personaje si la obra
# los usa muchas veces como hablante ("—dijo la chica" 5 veces o más) y como sujeto.
ANIMATE_NOUNS = set("""
chica chico silueta sombra voz figura mujer hombre joven anciano anciana viejo vieja niña niño criatura bestia
dragón diosa dios demonio muchacha muchacho gato gata lobo ser entidad presencia
""".split())
NEUTRAL_NOUNS = {"silueta", "sombra", "voz", "figura", "criatura", "presencia", "entidad", "bestia", "ser"}
# Solo estas palabras pueden ser un epíteto aprendido («el dragón» = Ddraig). «El pelo», «la puerta» o
# «el carro» al inicio de una frase no son personas y antes se aprendían por error.
EPITHET_NOUNS = ROLE_NOUNS | ANIMATE_NOUNS | set("""
monstruo diablo demonia ángel espíritu fantasma señor señora hermano hermana madre padre hijo hija abuelo
abuela compañero compañera enemigo enemiga invitado invitada humano humana elfo elfa enano enana vampiro
vampira titán titánide bebé pequeño pequeña rubia rubio morena moreno pelirroja pelirrojo castaña castaño
pelinegro pelinegra peliblanco peliblanca peliplateado peliplateada dragona guardián guardiana héroe heroína
diosecilla dueño dueña alumno alumna estudiante novio novia esposo esposa amigo amiga rival tirano tirana
""".split())


def epithet_ok(ep):
    p = ep.lower().split()
    return len(p) == 2 and p[0] in ("el", "la") and p[1] in EPITHET_NOUNS


def _alts(words):
    return "|".join(sorted(words, key=lambda w: (-len(w), w)))


ANIMATE_PHRASE = r"(?:[Ee]l|[Ll]a)\s+(?:" + _alts(ANIMATE_NOUNS) + r")\b(?!\s+(?:de|del)\b)"
ANIMATE_RE = re.compile(ANIMATE_PHRASE)


def is_role(name):
    p = name.split()
    return len(p) == 2 and p[0] in ("El", "La") and (p[1] in ROLE_NOUNS or p[1] in ANIMATE_NOUNS)


TITLES = {"Señor", "Señora", "Don", "Doña", "Lady", "Lord", "Sir", "Rey", "Reina", "Príncipe",
          "Princesa", "Capitán", "Capitana", "Comandante", "General", "Maestro", "Maestra",
          "Doctor", "Doctora", "Profesor", "Profesora", "Madre", "Padre", "Hermano", "Hermana",
          "Tío", "Tía", "Abuelo", "Abuela", "Santo", "Santa", "Sensei", "Lady", "Mister", "Miss"}
OBJECT_PREP = {"a", "al", "de", "del", "con", "para", "por", "hacia", "sobre", "contra", "sin",
               "ante", "tras", "entre", "que", "como", "según", "junto", "frente",
               "hasta", "desde", "mediante"}

FEMALE_NAMES = set("""
metis temis eris éride nix nyx hemera gea tetis tethys febe mnemósine mnemosine tea hécate hecate
estigia lete keres ker pandora selene eos asteria leto dione maya calipso psique hebe ilitía
átropos atropos cloto láquesis laquesis anfitrite climene clímene euríbia temisto nemesis némesis astrea irene eunomía dike tique
historia mikasa annie sasha hange hanji ymir frieda carla pieck gabi columbina raiden ei nahida
furina yoimiya ganyu keqing hutao paimon lumine beatriz elizabeth diane merlin noelle mimosa
nami robin boa hancock hinata sakura ino tsunade nezuko shinobu mitsuri kanao rem ram emilia
megumin aqua darkness asuna isabel maria maría carmen lucia lucía sofia sofía elena ana laura
marta paula irene alba noa eva inés ines beatrice yor anya makima power himeno reze kaguya chika
miku nino ichika yotsuba itsuki kaori rin sayaka hera rea rhea gea gaia deméter demeter hestia
perséfone persefone atenea afrodita artemisa nyx nix medusa lilith frieren fern aura ubel serie
violet marin mai nobara maki yuki kurumi erza lucy wendy juvia mirajane levy rias akeno koneko
zero tohru kanna mitsuha nagisa yumeko alice alicia saber rin sakura shiro kurisu mayuri
raiden shenhe yelan nilou navia clorinde arlecchino chiori citlali mavuika xilonen
""".split())
MALE_NAMES = set("""
caronte tártaro éter eter nereo hipnos tánatos thanatos érebo erebo urano ponto océano oceano
hiperión hiperion ceo crío crio jápeto japeto atlas prometeo epimeteo dionisio momo eros helios
tifón tifon minos radamantis éaco aeaco cerbero abel caín heracles hércules perseo aquiles
orfeo morfeo oneiros fobos deimos tritón triton proteo ladón
eren armin levi reiner bertholdt zeke erwin jean connie grisha kenny floch porco marcel joshua
nikola ezra sasuke naruto itachi kakashi tanjiro zenitsu inosuke rengoku giyu luffy zoro sanji
usopp asta yuno meliodas ban king escanor venti zhongli childe tartaglia diluc kaeya aether xiao
neuvillette wriothesley hades zeus poseidón poseidon cronos crono hermes apolo ares hefesto kuon
aurelio kessler valerius denji aki kishibe tomás nicolás jesús josé mateo dante goku vegeta gohan
kirito subaru kazuma rimuru ainz gojo yuji megumi sukuna toji geto natsu gray gajeel lucas
thorfinn guts griffith edward alphonse roy light ryuk lelouch suzaku shinji gintoki okabe
killua gon kurapika leorio hisoka saitama genos mob spike jotaro dio giorno ichigo aizen
alhaitham cyno tighnari kaveh lyney freminet sigewinne kinich mualani kirara
""".split())


def is_known_name(w):
    lw = w.lower()
    return lw in FEMALE_NAMES or lw in MALE_NAMES or plain(lw) in DICT_F or plain(lw) in DICT_M


def guess_gender_by_name(name):
    if is_role(name):
        w = name.split()[1]
        if w in NEUTRAL_NOUNS:
            return "M", 0.3          # "la voz", "la silueta": el artículo no dice el género
        fem = name.startswith("La") or w in ("reina", "princesa", "emperatriz", "monja", "diosa")
        return ("F", 10) if fem and w != "dios" else ("M", 10)
    n = name.split()[0].lower()
    if n in FEMALE_NAMES:
        return "F", 10
    if n in MALE_NAMES:
        return "M", 10
    p = plain(n)
    if p in DICT_F:
        return "F", 8
    if p in DICT_M:
        return "M", 8
    if n.endswith(("ia", "ina", "ella", "elle", "ette", "ita", "ana")):
        return "F", 2
    if n.endswith(("a", "ko", "mi", "ka", "ne", "ri", "ie", "ah", "ys", "is")):
        return "F", 1
    if n.endswith(("o", "os", "us", "ro", "to", "ki", "el", "an", "on", "or", "er")):
        return "M", 1
    return "M", 0.3


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------

# Qué tan seguro está Kuon IA según cómo dedujo al hablante
CONF = {
    "manual": 1.0, "guion": 0.98, "inciso": 0.95, "narrador (yo)": 0.95, "anuncio": 0.88,
    "voz descrita": 0.85, "sujeto": 0.78, "acción": 0.72, "réplica": 0.66, "conversación": 0.62,
    "párrafo previo": 0.6, "pronombre": 0.5, "nombre nuevo": 0.5, "sujeto de la escena": 0.45, "grupo": 0.9, "": 0.0,
}


@dataclass
class Segment:
    kind: str                 # 'narr' | 'dlg' | 'title'
    text: str
    start: int
    end: int
    speaker: str = ""
    hint: str = ""            # 'M'/'F' por pronombre, 'G' grupo, 'S' sujeto, 'YO' narrador
    emotion: str = "neutral"
    mood: str = "calma"
    pause: str = "inner"      # inner | para | title | scene
    source: str = ""          # cómo se decidió el hablante
    key: str = ""             # identificador estable para correcciones manuales
    okey: str = ""            # clave antigua (texto + contador), solo para convertir correcciones guardadas
    conf: float = 0.0
    guess: str = ""           # a quién creía Kuon IA cuando no estaba seguro


@dataclass
class Character:
    name: str
    lines: int = 0
    gender: str = "M"
    confidence: float = 0.0
    age: str = "adulto"       # niño | joven | adulto | anciano
    aliases: set = field(default_factory=set)
    evidence: dict = field(default_factory=dict)   # pistas de género: fem, mas, name, from


def new_memory():
    """Lo que Kuon IA aprende de tus correcciones (se guarda entre sesiones)."""
    return {"aliases": {}, "not_alias": [], "genders": {}, "not_names": [], "verbs": [], "style": {}, "calib": {},
            "epithets": {}, "stats": {"corrections": 0}}


# ---------------------------------------------------------------------------
# Párrafos
# ---------------------------------------------------------------------------

DASH_CHARS = "—–―"
QUOTE_RE = re.compile(r"«[^»\n]{1,}»|“[^”\n]{1,}”|\"[^\"\n]{1,}\"|‘[^’\n]{2,}’")
HYPHEN_SPLIT = re.compile(r"(?<=\s)-(?=\S)|(?<=\S)-(?=[\s.,;:])")
DASH_SPLIT = re.compile(r"[" + DASH_CHARS + r"]")
SCRIPT_RE = re.compile(
    r"^\s*\[?([A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü'’\-]*(?:\s[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü'’\-]*){0,2})\]?"
    r"\s*(\([^)\n]{0,40}\))?\s*(?:[:：]\s+|\.\s+(?=[(¿¡\"“«—–]))(\S.*)$")


def _trim(text, a, b, lead=" \t.,;:"):
    while a < b and text[a] in lead:
        a += 1
    while b > a and text[b - 1] in " \t":
        b -= 1
    return a, b


def _pieces(line, base):
    """Divide una línea en (kind, start, end) con posiciones absolutas."""
    s = line.lstrip()
    lead = len(line) - len(s)
    if not s:
        return []
    out = []
    if s[0] in DASH_CHARS or (s[0] == "-" and len(s) > 1 and s[1] not in "-"):
        splitter = DASH_SPLIT if s[0] in DASH_CHARS else HYPHEN_SPLIT
        pos = lead + 1
        bounds = [m.start() for m in splitter.finditer(line, pos)]
        edges = [pos] + [b + 1 for b in bounds]
        ends = bounds + [len(line)]
        for i, (a, b) in enumerate(zip(edges, ends)):
            a2, b2 = _trim(line, a, b, " \t.,;:" if i else " \t")
            if b2 > a2 and re.search(r"\w", line[a2:b2]):
                out.append(("dlg" if i % 2 == 0 else "narr", base + a2, base + b2))
        return out
    quotes = list(QUOTE_RE.finditer(line))
    if quotes:
        pos = lead
        for m in quotes:
            a2, b2 = _trim(line, pos, m.start())
            if b2 > a2 and re.search(r"\w", line[a2:b2]):
                out.append(("narr", base + a2, base + b2))
            out.append(("dlg", base + m.start() + 1, base + m.end() - 1))
            pos = m.end()
        a2, b2 = _trim(line, pos, len(line))
        if b2 > a2 and re.search(r"\w", line[a2:b2]):
            out.append(("narr", base + a2, base + b2))
        return out
    a2, b2 = _trim(line, lead, len(line), " \t")
    return [("narr", base + a2, base + b2)]


# ---------------------------------------------------------------------------
# Nombres
# ---------------------------------------------------------------------------

def canonical(name):
    parts = name.split()
    if len(parts) > 1 and parts[0] in TITLES:
        return " ".join(parts[:2])
    return parts[0]


def _prev_word(text, idx):
    m = re.search(r"(\w+)\W*$", text[max(0, idx - 20):idx])
    return m.group(1).lower() if m else ""


GROUP_WORDS = {"todos", "todas", "ambos", "ambas", "varios", "varias", "ellos", "ellas", "they",
               "algunos", "muchos", "algunas", "muchas", "nosotros"}
FIRST_PERSON = re.compile(
    r"^\s*(?:dije|respond[ií]|pregunt[eé]|grit[eé]|susurr[eé]|murmur[eé]|contest[eé]|añad[ií]|"
    r"repliqu[eé]|exclam[eé]|insist[ií]|suspir[eé]|coment[eé]|ment[ií]|ped[ií]|admit[ií]|"
    r"confes[eé]|expliqu[eé]|aclar[eé]|ped[ií]|balbuc[eé]|musit[eé]|repet[ií]|habl[eé]|pens[eé])\b",
    re.IGNORECASE)


class Rx:
    """Expresiones regulares con los verbos aprendidos incluidos."""

    def __init__(self, extra_verbs=()):
        extra = [re.escape(v) for v in extra_verbs if v and len(v) > 2]
        verb = SPEECH_VERB if not extra else r"(?:" + SPEECH_VERB + "|" + "|".join(extra) + r")"
        self.verb = verb
        self.speech = re.compile(r"\b" + verb + r"\b", re.IGNORECASE)
        self.name_verb = re.compile(r"(" + NAME_TOKEN + r")\s+(?:le |les |lo |la |se |me |te |nos )?"
                                    + verb + r"\b")
        self.dyn = set()
        self.dyn_verb = re.compile(r"(?:^|[.!?…—]\s*)(" + ANIMATE_PHRASE + r")\s+(?:le |les |lo |la |se |me |te |nos )?"
                                   + verb + r"\b")
        self.role_verb = re.compile(r"(?:^|[.!?…—]\s*)(" + ROLE_PHRASE + r")\s+(?:le |les |lo |la |se |me |te |nos )?"
                                    + verb + r"\b")


DEFAULT_RX = Rx()
RE_SPEECH = DEFAULT_RX.speech


def explicit_speaker(narr, rx=DEFAULT_RX):
    """Busca 'dijo Eren' / 'Eren dijo' / 'dijo ella' / 'dije' en un inciso."""
    if FIRST_PERSON.match(narr):
        return "", "YO"
    for vm in rx.speech.finditer(narr):
        tail = narr[vm.end(): vm.end() + 45]
        toks = re.finditer(r"[\wáéíóúñüÁÉÍÓÚÑÜ'’\-]+", tail)
        for n, tm in enumerate(toks):
            if n > 4:
                break
            w = tm.group(0)
            if re.search(r"[.;:!?]", tail[:tm.start()]):
                break
            if w[0].isupper() and w not in STOP:
                pw = _prev_word(tail, tm.start())
                if pw in OBJECT_PREP and n > 0:
                    break
                nxt = re.match(r"\s+(" + NAME_TOKEN + r")", tail[tm.end():])
                full = w + (" " + nxt.group(1) if nxt and w in TITLES else "")
                return canonical(full), ""
            lw = w.lower()
            if lw in ("que", "quien", "cuyo", "cuya"):
                break
            if lw in ("el", "la"):
                rm = ROLE_RE.match(tail, tm.start())
                if rm:
                    return role_name(rm.group(0)), ""
                if rx.dyn and _prev_word(tail, tm.start()) not in OBJECT_PREP:   # no "con la voz firme"
                    gm = ANIMATE_RE.match(tail, tm.start())
                    if gm and role_name(gm.group(0)) in rx.dyn:
                        return role_name(gm.group(0)), ""

            if lw in GROUP_WORDS:
                return "", "G"
            if lw in ("ella", "she"):
                return "", "F"
            if lw in ("él", "he"):
                return "", "M"
        if vm.start() < 4 and re.match(r"\s*(?:[.,;:]|$|con\b|en\b|sin\b|despacio|lento|bajo|"
                                       r"de nuevo|otra vez|al fin|por fin|mientras|y\b|entre\b)", tail):
            return "", "S"
    for m in rx.name_verb.finditer(narr):
        w = m.group(1)
        if w not in STOP and _prev_word(narr, m.start()) not in OBJECT_PREP:
            return canonical(w), ""
    rm = rx.role_verb.search(narr)
    if rm:
        return role_name(rm.group(1)), ""
    if rx.dyn:
        for gm in rx.dyn_verb.finditer(narr):
            if role_name(gm.group(1)) in rx.dyn:
                return role_name(gm.group(1)), ""
    if re.search(r",\s*\w+?(?:ada|ida|osa|ienta)\b", narr):
        return "", "F"
    if re.search(r",\s*\w+?(?:ado|ido|oso|iento)\b", narr):
        return "", "M"
    return "", ""


def first_known(text, known):
    """Primer personaje conocido en posición de sujeto dentro del texto."""
    for m in RE_CAP.finditer(text):
        w = m.group(0)
        if w in known and _prev_word(text, m.start()) not in OBJECT_PREP:
            return known[w]
    return ""


PRON_START = re.compile(r"\s*[\"“«]?(Ella|Él|She|He)\b")
SECOND_PERSON = re.compile(
    r"\b(?:tú|te|ti|tu|tus|contigo|eres|estás|tienes|quieres|puedes|sabes|vas|crees|piensas|"
    r"haces|harás|irás|serás|tardarás|podrás|querrás|necesitas|buscas|dices|vienes|estabas|"
    r"\w+(?:arás|erás|irás|aste|iste))\b", re.IGNORECASE)


def vocatives(dlg, known):
    out = set()
    for m in RE_CAP.finditer(dlg):
        w = m.group(0)
        if w not in known:
            continue
        before = dlg[max(0, m.start() - 3):m.start()]
        after = dlg[m.end():m.end() + 2]
        if (m.start() == 0 or re.search(r"[,¡¿]\s*$", before)) and re.match(r"\s*[,.!?…]", after or "."):
            out.add(known[w])
    return out


EPITHET_RE = re.compile(r"(?:^|[.!?…]\s+)((?:El|La)\s+[a-záéíóúñ]{3,})\s+(?!de\b|del\b|que\b)")


def learn_from_correction(memory, project, text, segs, seg, name):
    """
    Aprende de una corrección manual:
      - un epíteto ("el demonio", "el chico") que precede al diálogo y se refiere a 'name',
      - un verbo de habla nuevo ("graznó Mefistófeles").
    Devuelve una lista de cosas aprendidas (para mostrar).
    """
    learned = []
    memory.setdefault("stats", {}).setdefault("corrections", 0)
    memory["stats"]["corrections"] += 1
    if seg.source and seg.source not in ("manual", "guion"):
        src = seg.source.replace(" + estilo", "")
        cal = memory.setdefault("calib", {}).setdefault(project or "_", {}).setdefault(src, [0, 0])
        if (seg.speaker or seg.guess or "") == (name or ""):
            cal[0] += 1
        else:
            cal[1] += 1
            learned.append(f"menos confianza en «{src}»")
    if not name:
        return learned
    i = next((k for k, s in enumerate(segs) if s is seg), None)
    if i is None:
        return learned
    # verbo de habla nuevo en el inciso pegado al diálogo
    for s in segs[i + 1:i + 3]:
        if s.kind == "narr" and s.pause in ("inner", "para") and len(s.text) < 70:
            m = re.match(r"^\s*([a-záéíóúñ]{3,})\s+(?:\w+\s+)?" + re.escape(name.split()[0]), s.text)
            if m and not RE_SPEECH.match(m.group(1)) and m.group(1) not in memory["verbs"]:
                memory["verbs"].append(m.group(1))
                learned.append(f"verbo «{m.group(1)}»")
            break
    # epíteto en la narración previa
    ctx = ""
    for s in reversed(segs[max(0, i - 4):i]):
        if s.kind == "narr":
            ctx = s.text + " " + ctx
        elif s.kind == "dlg":
            break
    eps = [e for e in (m.group(1).lower() for m in EPITHET_RE.finditer(ctx[-400:])) if epithet_ok(e)]
    if eps and name.split()[0] not in ctx:
        ep = eps[-1]
        memory.setdefault("epithets", {}).setdefault(project or "_", {})[ep] = name
        learned.append(f"«{ep}» = {name}")
    return learned


def fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def phon(s):
    """Clave fonética simple para variantes de un mismo nombre (y/i, ph/f, th/t, k/c, letras dobles)."""
    t = fold(s)
    for a, b in (("ph", "f"), ("th", "t"), ("y", "i"), ("k", "c"), ("z", "s"), ("v", "b"), ("h", "")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def auto_aliases(names, total, memory):
    """Mefisto → Mefistófeles, Señor Hades → Hades: mismo personaje con nombre corto o con título."""
    out = {}
    blocked = set(tuple(x) for x in memory.get("not_alias", []))
    ns = sorted(names, key=lambda x: (len(x), x))
    for a in ns:
        if a in out:
            continue
        parts = a.split()
        if len(parts) == 2 and parts[0] in TITLES and parts[1] in names and (a, parts[1]) not in blocked:
            out[a] = parts[1]
            continue
        if len(a) < 4:
            continue
        fa = fold(a)
        pa = phon(a)
        twin = next((b for b in ns if b != a and " " not in b and phon(b) == pa and (a, b) not in blocked
                     and total.get(b, 0) > total.get(a, 0)), None)
        if twin:
            out[a] = twin          # Hypnos = Hipnos, Nyx = Nix: misma persona escrita distinto
            continue
        for b in ns:
            fb = fold(b)
            if b == a or not fb.startswith(fa) or len(b) <= len(a) + 1 or " " in b:
                continue
            if fb in (fa + "s", fa + "es"):
                continue        # plural, no apodo
            if (a, b) in blocked:
                continue
            ga, _ = guess_gender_by_name(a)
            gb, _ = guess_gender_by_name(b)
            if ga == gb and total.get(b, 0) >= total.get(a, 0) * 0.25:
                out[a] = b
                break
    return out


# ---------------------------------------------------------------------------
# Emoción y ánimo
# ---------------------------------------------------------------------------

EMO_PATTERNS = [
    ("grito", r"grit|bram|rugi|ruge|vocifer|chill|aull|vocear"),
    ("susurro", r"susurr|murmur|musit|cuchiche|en voz baja|bisbis"),
    ("pensamiento", r"pens[óa]|pensaba|se dijo|reflexion|para sí"),
    ("triste", r"sollo|llor|gimi|gime|entre lágrimas|con la voz rota|quebrad"),
    ("alegre", r"\bri[óo]\b|reía|carcaj|sonri|brome|divertid|alegre"),
    ("enojo", r"gruñ|espet|mascull|escupi|furios|con rabia|con furia|iracund|rabios"),
    ("duda", r"titube|balbuc|tartamud|vacil|dud[óa]"),
    ("frio", r"con frialdad|fríamente|gélid|sin emoción|cortante|seco"),
]
# (velocidad %, tono Hz, volumen %) con intensidad 100 %. El tono se mueve poco porque las voces
# neurales suenan artificiales con cambios grandes de tono; la emoción viene sobre todo del ritmo y la fuerza.
EMOTIONS = {
    "neutral": (0, 0, 0),
    "grito": (9, 3, 20),
    "exclamacion": (5, 2, 9),
    "susurro": (-9, -2, -32),
    "pensamiento": (-6, -1, -18),
    "triste": (-15, -3, -7),
    "alegre": (8, 3, 5),
    "enojo": (6, -2, 16),
    "duda": (-11, 0, -5),
    "pregunta": (0, 2, 0),
    "frio": (-6, -2, -5),
    "miedo": (7, 2, -8),
    "ternura": (-10, 1, -12),
    "sorpresa": (7, 4, 8),
}
EMO_LABEL = {
    "neutral": "neutral", "grito": "grito", "exclamacion": "exclamación", "susurro": "susurro",
    "pensamiento": "pensamiento", "triste": "tristeza", "alegre": "alegría", "enojo": "enojo",
    "duda": "duda", "pregunta": "pregunta", "frio": "frialdad", "miedo": "miedo",
    "ternura": "ternura", "sorpresa": "sorpresa",
}
INCISO_EMOS = {"grito", "susurro", "pensamiento", "triste", "alegre", "enojo", "duda", "frio"}

# Léxico emocional por oración (texto en minúsculas)
SENT_LEX = [
    ("miedo", r"\b(?:miedo|terror|aterr\w*|pánico|horror|espant\w*|asust\w*|temblando|socorro|auxilio|"
              r"no me hagas|por favor no|aléjate|aléjense|huye|huyan|corre|corran)\b"),
    ("enojo", r"\b(?:maldit\w*|maldición|idiota|imbécil|estúpid\w*|odio|te odio|furia|rabia|cállate|"
              r"basta ya|desgraciad\w*|bastard\w*|mátenlo|mátalo|cómo te atreves|lárgate|inútil|miserable)\b"),
    ("triste", r"\b(?:lo siento|perdóname|perdón|adiós|te extraño|lo extraño|la extraño|murió|está muerto|"
               r"está muerta|ya no está|nunca más|me duele|duele|lágrimas|llorar|sola|solo otra vez)\b"),
    ("ternura", r"\b(?:te quiero|te amo|cariño|mi amor|amor mío|querida|querido|mi vida|tranquila|tranquilo|"
                r"descansa|estoy aquí|no tengas miedo|todo estará bien|shh+)\b"),
    ("alegre", r"\b(?:ja(?:ja)+|je(?:je)+|genial|increíble|maravillos\w*|qué bien|por fin|lo logramos|"
               r"felicidades|gracias|me alegra|qué alegría|hurra|bravo|excelente)\b"),
    ("sorpresa", r"\b(?:imposible|no puede ser|en serio|de verdad|cómo es posible|qué demonios|qué rayos|"
                 r"qué diablos|no lo puedo creer|espera|un momento)\b"),
]
SENT_LEX_RX = [(e, re.compile(p)) for e, p in SENT_LEX]
SENT_SPLIT = re.compile(r"(?<=[.!?…»”])\s+")


def sentence_emotion(sent, base):
    """Emoción de una oración de diálogo según cómo está escrita."""
    if base in INCISO_EMOS:
        return base
    t = sent.strip()
    low = t.lower()
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÑ]", "", t)
    if len(letters) > 6 and letters.isupper():
        return "grito"
    lex = ""
    for emo, rxe in SENT_LEX_RX:
        if rxe.search(low):
            lex = emo
            break
    tail = t.rstrip("»”\"' ")
    excl = "!" in tail[-2:] or t[:2].startswith("¡") or "¡" in t[:2]
    ques = "?" in tail[-2:] or "¿" in t[:2]
    ell = tail.endswith("...") or tail.endswith("…")
    if excl and ques:
        return "sorpresa"
    if excl:
        if lex in ("enojo", "miedo", "alegre", "sorpresa"):
            return lex
        return "grito" if t.count("!") >= 2 else "exclamacion"
    if ques:
        return "sorpresa" if lex == "sorpresa" else "pregunta"
    if ell:
        return lex or "duda"
    return lex or (base if base in EMOTIONS else "neutral")


def sentence_extra(sent):
    """Ajuste para una oración de narración: (velocidad, tono, volumen, pausa extra ms)."""
    t = sent.strip().rstrip("»”\"' ")
    if t.endswith("...") or t.endswith("…"):
        return (-6, 0, -2, 320)
    if t.endswith("!"):
        return (3, 1, 5, 60)
    if t.endswith("?"):
        return (0, 1, 0, 120)
    return (0, 0, 0, 0)


def detect_emotion(dlg, narr_context):
    ctx = narr_context.lower()
    for emo, pat in EMO_PATTERNS:
        if re.search(pat, ctx):
            return emo
    t = dlg.strip()
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÑ]", "", t)
    if len(letters) > 6 and letters.isupper():
        return "grito"
    if t.endswith(("!", "!»", "!”")) or t.startswith("¡"):
        return "grito" if t.count("!") >= 2 else "exclamacion"
    if t.endswith(("...", "…")):
        return "duda"
    if t.endswith("?") or t.startswith("¿"):
        return "pregunta"
    return "neutral"


MOODS = {
    "accion": ("golpe", "espada", "explosi", "estall", "corri", "salt", "atac", "bloque",
               "esquiv", "sangre", "rugi", "dispar", "impact", "puñetazo", "patada", "llamas",
               "rayo", "trueno", "choque", "embist", "filo", "batalla", "combate", "lanzó",
               "destroz", "aplast", "golpeó", "cortó", "atravesó", "titán", "relámpago"),
    "tension": ("silencio", "oscur", "sombra", "susurr", "frío", "helad", "miedo", "temblo",
                "acech", "lentamente", "contuvo", "respiraci", "pasos", "crujid", "inquiet",
                "amenaz", "presenci", "escalofr", "niebla", "vacío", "terror", "pánico"),
    "triste": ("lágrima", "llor", "llant", "sollo", "muert", "murió", "tumba", "funeral",
               "dolor", "pérdida", "adiós", "despedi", "luto", "nunca más", "lamento",
               "cadáver", "duelo"),
    "romance": ("mejilla", "sonroj", "latido", "abrazo", "abrazó", "beso", "besó", "labios",
                "ternura", "caricia", "acarici", "su mano", "calidez", "cálid", "suave"),
}
MOOD_PROSODY = {"calma": (0, 0, 0), "accion": (6, 1, 3), "tension": (-7, -2, -3),
                "triste": (-9, -2, -3), "romance": (-5, 1, -3)}


def detect_mood(text):
    t = text.lower()
    best, score = "calma", 0
    for mood, words in MOODS.items():
        s = sum(t.count(w) for w in words)
        if s > score:
            best, score = mood, s
    if score >= 2 or (score == 1 and len(t) < 180):
        return best
    return "calma"


# ---------------------------------------------------------------------------
# Género y edad
# ---------------------------------------------------------------------------

FEM_NOUN = r"(?:joven|chica|niña|muchacha|mujer|reina|princesa|señora|diosa|hermana|madre|" \
           r"doncella|capitana|anciana|guerrera|maga|bruja|sacerdotisa|dama|señorita|chiquilla|" \
           r"emperatriz|hechicera|heroína|soldado|cazadora|teniente|líder)"
MAS_NOUN = r"(?:joven|chico|niño|muchacho|hombre|rey|príncipe|señor|dios|hermano|padre|" \
           r"capitán|anciano|guerrero|mago|brujo|sacerdote|caballero|chiquillo|emperador|" \
           r"hechicero|héroe|soldado|cazador|teniente|comandante|líder|general)"


_END_CACHE = {}


def _ends_with(src, text, end, flags=0, span=40):
    """¿Termina en text[:end] algo que encaja con src? Igual que buscar src justo antes de una coincidencia,
    pero sin recorrer todo el texto con una expresión enorme (los verbos de habla son cientos de formas)."""
    key = (src, flags)
    r = _END_CACHE.get(key)
    if r is None:
        r = _END_CACHE[key] = re.compile(r"(?:" + src + r")$", flags)
    a = max(0, end - span)
    while 0 < a < end and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a += 1
    return r.search(text[a:end]) is not None


_OCC = {"text": None, "occ": {}}


def _occurrences(text, word):
    """Posiciones de word como palabra completa (igual que \\bword\\b), con str.find y caché por texto:
    recorrer 500.000 palabras con una expresión por personaje era lo más lento del análisis."""
    if _OCC["text"] is not text:
        _OCC["text"], _OCC["occ"] = text, {}
    got = _OCC["occ"].get(word)
    if got is not None:
        return got
    isw = lambda c: c.isalnum() or c == "_"
    out, i, n, L = [], text.find(word), len(word), len(text)
    first, last = isw(word[0]), isw(word[-1])
    while i >= 0:
        before = isw(text[i - 1]) if i > 0 else False
        after = isw(text[i + n]) if i + n < L else False
        if before != first and after != last:
            out.append(i)
        i = text.find(word, i + 1)
    _OCC["occ"][word] = out
    return out


def _windows(text, name, span=220, cap=160):
    occ = list(_occurrences(text, name.split()[0]))
    if len(occ) > cap:
        step = len(occ) / cap
        occ = [occ[int(i * step)] for i in range(cap)]
    return "\n¶\n".join(text[max(0, o - span): o + span] for o in occ), len(occ)


def score_gender(text, name, detail=None):
    """Suma evidencias del contexto. Positivo = femenino, negativo = masculino.
    detail (dict opcional) recibe las pistas: fem, mas (peso de cada lado en el texto) y name."""
    if is_role(name):
        g, w = guess_gender_by_name(name)
        if detail is not None:
            detail.update(fem=0.0, mas=0.0, name=g, name_w=w)
        return w if g == "F" else -w
    n = re.escape(name.split()[0])
    text, _ = _windows(text, name)
    s = 0.0
    fem = mas = 0.0

    def add(w, k):
        nonlocal s, fem, mas
        s += w * k
        if w > 0:
            fem += w * k
        else:
            mas -= w * k
    add(3, len(re.findall(r"\bla\s+" + FEM_NOUN + r"\s+(?:\w+\s+)?" + n + r"\b", text)))
    add(-3, len(re.findall(r"\bel\s+" + MAS_NOUN + r"\s+(?:\w+\s+)?" + n + r"\b", text)))
    add(2, len(re.findall(r"\b" + n + r"\b[^.\n]{0,40}?\b(?:estaba|era|parecía|se sentía|se veía|"
                            r"quedó|se quedó|seguía)\s+(?:muy\s+|tan\s+|algo\s+|un poco\s+)?"
                            r"\w+(?:ada|ida|osa|ienta|ita|a)\b", text)))
    add(-2, len(re.findall(r"\b" + n + r"\b[^.\n]{0,40}?\b(?:estaba|era|parecía|se sentía|se veía|"
                            r"quedó|se quedó|seguía)\s+(?:muy\s+|tan\s+|algo\s+|un poco\s+)?"
                            r"\w+(?:ado|ido|oso|iento|ito)\b", text)))
    add(1.5, sum(1 for m in re.finditer(r"\s+" + n + r"\s*,\s*\w+(?:ada|ida|osa|ienta)\b", text)
                 if _ends_with(SPEECH_VERB, text, m.start())))
    add(-1.5, sum(1 for m in re.finditer(r"\s+" + n + r"\s*,\s*\w+(?:ado|ido|oso|iento)\b", text)
                  if _ends_with(SPEECH_VERB, text, m.start())))
    # pronombre en la oración siguiente a una en la que el nombre es sujeto
    for m in re.finditer(r"(?:^|[.!?]\s+)" + n + r"\b[^.!?\n]{0,160}[.!?]\s+(Ella|Él)\b", text):
        add(0.8 if m.group(1) == "Ella" else -0.8, 1)
    fem_app = r"(?:diosa|mujer|reina|madre|hermana|esposa|hija|joven|chica|niña|princesa|señora|titánide|doncella)"
    mas_app = r"(?:dios|hombre|rey|padre|hermano|esposo|hijo|joven|chico|niño|príncipe|señor|titán|caballero|demonio)"
    add(3, len(re.findall(r"\b" + n + r"\b\s*[.,]\s*(?:La|Una|Su|la|una|su)\s+" + fem_app + r"\b", text)))
    add(-3, len(re.findall(r"\b" + n + r"\b\s*[.,]\s*(?:El|Un|Su|el|un|su)\s+" + mas_app + r"\b", text)))
    add(2, len(re.findall(r"\b(?:una|la)\s+" + fem_app + r"\b[^.\n]{0,90}[.,]\s*" + n + r"\b", text)))
    add(-2, len(re.findall(r"\b(?:un|el)\s+" + mas_app + r"\b[^.\n]{0,90}[.,]\s*" + n + r"\b", text)))
    g, w = guess_gender_by_name(name)
    s += w if g == "F" else -w
    if detail is not None:
        detail.update(fem=round(fem, 1), mas=round(mas, 1), name=g if w >= 1 else "", name_w=w)
    return s


def guess_age(text, name):
    if is_role(name):
        return "adulto"
    win, k = _windows(text, name, span=70, cap=200)
    win = win.lower()
    kid = len(re.findall(r"\bniñ[oa]s?\b|\bpequeñ[oa]\b|\bcrío\b|\bcría\b|chiquill|infant|\bbebé|mocos[oa]", win))
    old = len(re.findall(r"\bancian[oa]\b|\bviej[oa]\b|\babuel[oa]\b|\bcanos[oa]\b|arrugad", win))
    need = max(2, 0.18 * k)
    if kid >= need and kid > old * 1.5:
        return "niño"
    if old >= need and old > kid * 1.5:
        return "anciano"
    return "adulto"


# ---------------------------------------------------------------------------
# Aprendizaje del estilo de cada personaje
# ---------------------------------------------------------------------------

STYLE_STOP = set("""
de la que el en y a los se del las un por con no una su para es al lo como más pero sus le ya o este
sí si porque esta entre cuando muy sin sobre también me hasta hay donde quien desde todo nos durante
todos uno les ni contra otros ese eso ante ellos esto mí antes algunos qué unos yo otro otras otra él
tanto esa estos mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros mi mis tú
te ti tu tus ellas vosotros os ha he has han era fue ser soy eres está estoy estás son así bien ahora
aquí allí eh ah oh pues entonces cómo dónde cuándo porqué cuál quién
""".split())
STYLE_WORD = re.compile(r"[a-záéíóúñüàèìòùâêîôûäëïöçœæß]{2,}")
STRONG_SOURCES = {"manual", "guion", "inciso", "narrador (yo)", "grupo", "anuncio", "voz descrita"}


def style_tokens(text, voc=()):
    toks = [w for w in STYLE_WORD.findall(text.lower()) if w not in STYLE_STOP]
    toks += ["@voc:" + v for v in sorted(voc)]
    return toks


def _model_add(model, name, toks, weight=1):
    m = model.setdefault(name, {"n": 0, "w": {}})
    m["n"] += weight
    for t in toks:
        m["w"][t] = m["w"].get(t, 0) + weight


def _model_prune(model, top=150, min_lines=3):
    out = {}
    for name, m in model.items():
        if m["n"] < min_lines:
            continue
        items = sorted(m["w"].items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        out[name] = {"n": m["n"], "w": dict(items)}
    return out


def style_score(m, toks, n_total, k_chars):
    sc = 0.0                      # sin ventaja para el personaje que más habla
    tot = sum(m["w"].values())
    for t in toks:
        sc += math.log((m["w"].get(t, 0) + 0.5) / (tot + 2500.0))
    return sc


# ---------------------------------------------------------------------------
# Análisis completo
# ---------------------------------------------------------------------------

def _key_old(text, seen):
    k = re.sub(r"\W+", " ", text.lower()).strip()[:60]
    seen[k] += 1
    return f"{k}#{seen[k]}"


def _key(text, seen, before):
    """Clave de un diálogo: su texto más lo que hay justo antes. Así, agregar o borrar un «—¿Qué?» en otra
    parte del capítulo no mueve las correcciones (antes se usaba un contador que se corría)."""
    k = re.sub(r"\W+", " ", text.lower()).strip()[:60]
    c = re.sub(r"\W+", " ", before.lower()).strip()[-40:]
    base = k + "|" + c
    seen[base] += 1
    return base if seen[base] == 1 else f"{base}#{seen[base]}"


def _apply_key(seg, text, seen, seen_old, overrides):
    seg.key = _key(seg.text, seen, text[max(0, seg.start - 300):seg.start])
    seg.okey = _key_old(seg.text, seen_old)
    return overrides.get(seg.key, overrides.get(seg.okey)) if (seg.key in overrides or seg.okey in overrides) else None


ARTS = {"el", "la", "los", "las", "del", "al", "un", "una", "unos", "unas", "su", "sus", "mi",
        "mis", "tu", "tus", "este", "esta", "ese", "esa", "aquel", "aquella", "nuestro",
        "nuestra", "lo", "otro", "otra", "cada", "the", "an"}
SCRIPT_STOP = {"Nota", "Notas", "Capítulo", "Capitulo", "Escena", "Parte", "Autor", "Autora",
               "Título", "Titulo", "Advertencia", "Aviso", "Resumen", "Fin", "Personajes", "Fecha",
               "Lugar", "Tiempo", "Hora", "Ps", "PD", "Edit", "Importante", "Créditos", "Cap"}


# --- Modelo entrenado: elige quién habla cuando no hay acotación ("—dijo X").
WEAK_SOURCES = {"conversación", "réplica", "párrafo previo", "sujeto de la escena", "nombre nuevo", "pronombre"}
PLACE_PREP = {"en", "de", "del", "desde", "hacia", "hasta", "para", "por", "sobre", "a"}


ROLE_SIG = re.compile(r"(?:^|[.!?…]\s+)(" + ROLE_PHRASE + r")\s+(?:se\s+|le\s+|la\s+|lo\s+|no\s+)?"
                      r"[a-záéíóúñ]{2,}(?:ó|ió|aba|ía)\b", re.MULTILINE)
PERSON_SIG = re.compile(
    r"(?:^|[.!?…]\s+|,\s+|\by\s+)(" + NAME_TOKEN + r")\s+(?:se|le|la|lo|les|no|me|te|ya|también|nunca|siempre|todavía)"
    r"(?:\s+(?:se|le|la|lo|les|me|te))?\s+[a-záéíóúñ]{3,}"
    r"|(?:^|[.!?…]\s+|,\s+|\by\s+)(" + NAME_TOKEN + r")\s+[a-záéíóúñ]{2,}(?:ó|ió|aba|ía|aron|ieron|aban|ían)\b"
    r"|\b(?:a|con)\s+(" + NAME_TOKEN + r")(?=\s*[,.;:!?…]|\s+(?:y|que|con|sin|mientras)\b)"
    r"|\b(?:hermano|hermana|amigo|amiga|primo|prima|tío|tía|padre|madre|hijo|hija|esposo|esposa|novio|novia|"
    r"jefe|jefa|compañero|compañera|vecino|vecina|abuelo|abuela|señor|señora|don|doña)\s+(" + NAME_TOKEN + r")"
    r"|,\s*(" + NAME_TOKEN + r")\s*[.!?…]+(?=\s*[—–\"”»]|\s*$)"
    r"|(?:^|[—–«\"“])\s*[¡¿]?(" + NAME_TOKEN + r"),\s",
    re.MULTILINE)


_SKIP_TOK = {"x", "y", "cap", "capitulo", "capítulo", "bloque", "de", "del", "la", "el", "los", "las", "parte", "completo"}


def _ptoks(name):
    return {t for t in re.split(r"[^a-z0-9ñ]+", fold(name)) if t and t not in _SKIP_TOK and not t.isdigit()}


def merge_base(memory, base, project):
    """Memoria precargada (entrenada con las obras del usuario): estilo de cada personaje por obra,
    géneros seguros y apodos. No pisa nada de lo que el usuario corrigió."""
    if not base:
        return memory
    memory["base_genders"] = base.get("genders", {})
    memory["base_aliases"] = base.get("aliases", {})
    pt = _ptoks(project or "")
    if pt and not memory.get("style", {}).get(project):
        for k in base.get("style", {}):
            kt = _ptoks(k)
            if kt and (pt <= kt or kt <= pt):
                memory.setdefault("style", {})[project] = json.loads(json.dumps(base["style"][k]))
                break
    return memory


def clean_aliases(al):
    """Quita fusiones que se anulan entre sí (A = A, henrietta = Henrietta, A -> B -> A) y resuelve cadenas.
    Devuelve (alias limpios, {minúsculas: forma preferida}) para unificar nombres que solo cambian de mayúsculas."""
    canon = {}
    for k, v in al.items():
        if k and v and k != v and k.lower() == v.lower():
            pick = k if (k[:1].isupper() and not v[:1].isupper()) else v
            canon.setdefault(k.lower(), pick)
    out = {}
    for k, v in al.items():
        v2 = canon.get(v.lower(), v) if v else v
        if not k or not v2 or k.lower() == v2.lower():
            continue
        out[k] = v2
    res = {}
    for k in out:
        seen, v = {k}, out[k]
        while v in out and v not in seen and len(seen) < 12:
            seen.add(v)
            v = out[v]
        if v in seen or v in out:
            continue        # ciclo: ninguno de los dos nombres se fusiona
        res[k] = v
    return res, canon


def analyze(text, overrides=None, aliases=None, memory=None, project="", threshold=0.55, doc="", _eval=None):
    """
    Devuelve (segmentos, personajes, info).
    overrides: {key: 'Nombre' | ''}  correcciones manuales del documento.
    aliases:   {'Shorty': 'Levi'}    fusiones del documento.
    memory:    lo aprendido en sesiones anteriores (ver new_memory()).
    threshold: confianza mínima; por debajo, el diálogo lo lee el narrador.
    """
    memory = memory or new_memory()
    aliases, canon = clean_aliases({**memory.get("base_aliases", {}), **memory.get("aliases", {}), **(aliases or {})})

    def fix(n):
        n = canon.get(n.lower(), n)
        return aliases.get(n, n)
    overrides = {k: (fix(v) if v else v) for k, v in (overrides or {}).items()}
    mem_genders = {**memory.get("base_genders", {}), **memory.get("genders", {})}
    # lo que el usuario asigna o fusiona a mano siempre puede ser personaje, aunque antes lo descartara
    not_names = set(memory.get("not_names", [])) - {v for v in overrides.values() if v} - set(aliases.values())
    rx = Rx(memory.get("verbs", []))
    gen_inc = Counter(role_name(m.group(1)) for m in re.finditer(r"\s+(" + ANIMATE_PHRASE + r")", text, re.IGNORECASE)
                      if _ends_with(r"\b" + rx.verb, text, m.start(), re.IGNORECASE))
    gen_subj = Counter(role_name(m.group(1)) for m in re.finditer(r"(?:^|[.!?…]\s+)(" + ANIMATE_PHRASE + r")", text, re.M))
    rx.dyn = {n for n, k in gen_inc.items() if k >= 5 and gen_subj[n] >= 3 and n.split()[0] in ("El", "La")}
    epithets = {e: fix(n) for e, n in {**memory.get("epithets", {}).get("_", {}),
                                        **memory.get("epithets", {}).get(project or "_", {})}.items()
                if n and epithet_ok(e)}
    info = {"auto_aliases": {}, "protagonist": "", "first_person": False, "script": False,
            "style_model": {}, "style_changes": 0, "style_chars": 0}
    calib = memory.get("calib", {}).get(project or "_", {})

    # --- Formato guion ("Eren: texto")
    def norm_script(n):
        return n.title() if n.isupper() else n

    script_counts = Counter()
    for m in re.finditer(r"[^\n]+", text):
        sm = SCRIPT_RE.match(m.group(0))
        if sm:
            n0 = norm_script(sm.group(1))
            if n0.split()[0] not in SCRIPT_STOP and n0.split()[0] not in STOP:
                script_counts[n0] += 1
    script_total = sum(script_counts.values())
    script_names = {n for n, c in script_counts.items() if (c >= 2 or script_total >= 3)
                    and not fold(n).endswith(("cion", "sion", "dad", "miento", "mente"))}
    info["script"] = bool(script_names)

    paras = []
    for m in re.finditer(r"[^\n]+", text):
        line = m.group(0)
        if not line.strip():
            continue
        if BREAK_RE.match(line):
            paras.append(("break", m.start(), m.end(), []))
            continue
        if CHAPTER_RE.match(line):
            paras.append(("title", m.start(), m.end(), []))
            continue
        sm = SCRIPT_RE.match(line) if script_names else None
        if sm and norm_script(sm.group(1)) in script_names:
            nm = norm_script(sm.group(1))
            a2 = m.start() + sm.start(3)
            pcs = [("dlg", a2, m.end())]
            paras.append(("script", m.start(), m.end(), pcs, nm, sm.group(2) or ""))
            continue
        paras.append(("text", m.start(), m.end(), _pieces(line, m.start())))

    # --- Pasada 1: nombres seguros (incisos explícitos) y nombres frecuentes
    lower_cnt = Counter(re.findall(r"\b[a-záéíóúñü]+\b", text))
    lower_words = set(lower_cnt)
    anchors = Counter()
    yo_hits = 0
    for p in paras:
        kind, pcs = p[0], p[3]
        if kind == "script":
            anchors[p[4]] += 1
            continue
        if kind != "text" or not any(x[0] == "dlg" for x in pcs):
            continue
        for k, s, e in pcs:
            if k == "narr":
                nm, h = explicit_speaker(text[s:e], rx)
                if h == "YO":
                    yo_hits += 1
                if nm:
                    anchors[aliases.get(nm, nm)] += 1
                    break
    info["first_person"] = yo_hits >= 3

    total, artc, mid, prepc = Counter(), Counter(), Counter(), Counter()
    for m in RE_CAP.finditer(text):
        w = m.group(0)
        total[w] += 1
        j = m.start() - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        prevc = text[j] if j >= 0 else "\n"
        if prevc not in ".!?¡¿\"“«—–―\n:…":
            mid[w] += 1
        pw = _prev_word(text, m.start())
        if pw in ARTS and m.start() - len(pw) - 1 >= 0:
            artc[w] += 1
        if pw in PLACE_PREP:
            prepc[w] += 1

    def common_word(w):
        """¿Es una palabra común? ('ventanilla' minúscula 3 veces no impide que Ventanilla sea un nombre)."""
        if w.lower().endswith("mente"):
            return True
        if mid[w] < 2 and w.lower() in DICT_COMMON and not is_known_name(w):
            return True     # diccionario: "Despacio", "Repitió", "Mientes" al inicio de frase
        lc = lower_cnt[w.lower()]
        return lc > 0 and not (mid[w] >= 3 and mid[w] >= 3 * lc)

    def verb_like(w):
        """'Paré', 'Mientes', 'Simplemente' al inicio de frase: verbos y adverbios, no nombres."""
        lw = w.lower()
        if lw in FEMALE_NAMES or lw in MALE_NAMES or mid[w] >= 1:
            return False
        return lw.endswith(("mente", "es", "as", "é", "ó", "aste", "amos", "emos", "imos", "aron", "ieron", "aba", "ía"))

    def place_like(w):
        return total[w] >= 3 and prepc[w] / total[w] >= 0.4      # "en Paradis", "de Ehrmich": lugares

    person_sig = Counter()
    for m in PERSON_SIG.finditer(text):
        if m.group(6) and re.search(r"(?:te|me|se|nos|lo|la|le|las|los)$", m.group(6)):
            continue        # "Cállate, ...", "Mírame, ...": imperativos, no vocativos
        person_sig[m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6)] += 1
    known = {}
    for m in ROLE_SIG.finditer(text):      # "El posadero dejó el vaso": rol que actúa como personaje
        r_ = role_name(m.group(1))
        known[r_] = r_
    for r_ in sorted(rx.dyn):
        known[r_] = r_
    for nm in anchors:
        first = nm.split()[0]
        if nm in not_names or first in not_names:
            continue
        if nm in script_names or is_role(nm):
            known[nm] = nm
            continue
        if common_word(first) and nm not in aliases.values():
            continue        # "Despacio", "Bajito": adverbios, no nombres
        if artc[first] / max(total[first], 1) < 0.6 or anchors[nm] >= 3:
            known[first] = nm
            known[nm] = nm
    for w, c in total.items():
        if w in known or w in STOP or w in not_names or len(w) < 3 or w.isupper() \
                or common_word(w) or place_like(w):
            continue
        if artc[w] / c >= 0.34:
            continue        # "el Inframundo", "los Titanes": lugares, grupos, títulos
        if (c >= 3 and mid[w] >= 1) or mid[w] >= 2 or c >= 6:
            known[w] = w
        elif person_sig[w] >= 1 and not w.endswith(("mente", "ó", "aba", "aron", "ían")) and not verb_like(w):
            known[w] = w        # nombres que aparecen poco pero actúan como personas ("Sofía se detuvo")

    # "Eren Yeager" dos o más veces: el apellido es el mismo personaje
    pairs = Counter((m.group(1), m.group(2)) for m in re.finditer(r"(" + NAME_TOKEN + r") (" + NAME_TOKEN + r")\b", text))
    for (a_, b_), n_ in pairs.items():
        if n_ >= 2 and a_ in known and b_ in known and a_ != b_ and b_ not in anchors and a_ not in TITLES \
                and b_ not in aliases and known[a_] == a_:
            aliases[b_] = a_
    auto = auto_aliases({v for v in known.values()}, total, memory)
    for a_, b_ in auto.items():
        if a_ not in aliases:
            aliases[a_] = b_
    info["auto_aliases"] = auto
    for k_ in list(known):
        known[k_] = aliases.get(known[k_], known[k_])
    for a_, real in aliases.items():
        known[a_] = real
        if " " not in a_:
            known.setdefault(a_.split()[0], real)

    roles_known = {k for k in known.values() if is_role(k)}
    role_any = ROLE_PHRASE if not rx.dyn else r"(?:[Ee]l|[Ll]a)\s+(?:" + _alts(
        ROLE_NOUNS | {n.split()[1] for n in rx.dyn}) + r")\b(?!\s+(?:de|del)\b)"
    ROLE_ANY_RE = re.compile(role_any)
    ROLE_SUBJ = re.compile(r"(?:^|[.!?…]\s+|,\s+|\by\s+)(" + role_any + r")(?!\s*[.!?…])") if roles_known else None

    def names_in(t):
        out = [known[w] for w in (re.sub(r"['’]s$", "", m.group(0)) for m in RE_CAP.finditer(t)) if w in known]
        if ROLE_SUBJ:
            out += [r for r in (role_name(m.group(0)) for m in ROLE_ANY_RE.finditer(t)) if r in roles_known]
        return out

    SUBJ_RE = re.compile(r"(?:^|[.!?…]\s+|,\s+|;\s+|\by\s+)[\"“«]?(?:[Aa]\s+(?=" + NAME_TOKEN +
                         r"\s+(?:se|le|les)\s))?(" + NAME_TOKEN + r")(?!\s*[.!?…])")
    INTRO_RE = re.compile(r"(?:^|[.!?…]\s+)(" + NAME_TOKEN + r")\.")
    EPI_RE = None
    if epithets:
        alts = "|".join(sorted((re.escape(e) for e in epithets), key=len, reverse=True))
        EPI_RE = re.compile(r"(?:^|[.!?…]\s+|,\s+)(" + alts + r")\b", re.IGNORECASE)

    def subjects_in(t):
        """Nombres en posición de sujeto: inicio de oración o tras coma (y epítetos aprendidos)."""
        hits = []
        for m in SUBJ_RE.finditer(t):
            w = m.group(1)
            if w in known and not re.match(r"\s*,\s*(?:que|quien)\b", t[m.end():]):
                hits.append((m.start(1), known[w]))
        for m in INTRO_RE.finditer(t):
            if m.group(1) in known:
                hits.append((m.start(1), known[m.group(1)]))
        if EPI_RE:
            for m in EPI_RE.finditer(t):
                hits.append((m.start(1), epithets.get(m.group(1).lower(), "")))
        if ROLE_SUBJ:
            for m in ROLE_SUBJ.finditer(t):
                r = role_name(m.group(1))
                if r in roles_known:
                    hits.append((m.start(1), r))
        return [n for _, n in sorted(hits) if n]

    subj_count = Counter()
    for p in paras:
        if p[0] in ("text",):
            for k, s_, e_ in p[3]:
                if k == "narr":
                    for nm in subjects_in(text[s_:e_]):
                        subj_count[nm] += 1
    protagonist = subj_count.most_common(1)[0][0] if subj_count else ""
    info["protagonist"] = protagonist

    # --- Pasada 2: atribución secuencial con memoria de escena
    segs = []
    seen, seen_old = Counter(), Counter()
    recent = []          # hablantes de la conversación actual
    mentions = []        # personajes mencionados recientemente
    narr_mentions = []
    prev_narr_only = ""
    current_subject = protagonist
    last_dlg_segs = []
    genders_cache, score_cache = {}, {}

    def gender_of(nm):
        if nm not in genders_cache:
            if nm in mem_genders:
                genders_cache[nm] = mem_genders[nm]
            else:
                score_cache[nm] = score_gender(text, nm)
                genders_cache[nm] = "F" if score_cache[nm] > 0 else "M"
        return genders_cache[nm]

    def push_recent(nm):
        if nm:
            if not recent or recent[-1] != nm:
                recent.append(nm)
            if nm in mentions:
                mentions.remove(nm)
            mentions.append(nm)

    for p in paras:
        kind, a, b, pcs = p[0], p[1], p[2], p[3]
        if kind == "break":
            if segs:
                segs[-1].pause = "scene"
            recent, prev_narr_only = [], ""
            current_subject = protagonist
            continue
        if kind == "title":
            if segs:
                segs[-1].pause = "scene"
            segs.append(Segment("title", text[a:b].strip(" #*_"), a, b, pause="title", mood="calma"))
            recent, mentions, prev_narr_only = [], [], ""
            current_subject = protagonist
            continue
        if kind == "script":
            nm = aliases.get(p[4], p[4])
            _, s, e = pcs[0]
            t = text[s:e]
            seg = Segment("dlg", t, s, e, speaker=nm, source="guion", conf=CONF["guion"], pause="para")
            ov = _apply_key(seg, text, seen, seen_old, overrides)
            if ov is not None:
                seg.speaker, seg.source, seg.conf = ov, "manual", 1.0
            seg.emotion = detect_emotion(t, p[5])
            segs.append(seg)
            push_recent(seg.speaker)
            prev_narr_only = ""
            continue

        dlg = [x for x in pcs if x[0] == "dlg"]
        narr_txt = " ".join(text[s:e] for k, s, e in pcs if k == "narr")
        if not dlg and last_dlg_segs:
            rv = re.match(r"\s*(?:La|Esa|Aquella|Su)\s+voz\s+(?:de\s+)?(" + NAME_TOKEN + r")", narr_txt)
            if rv and rv.group(1) in known:
                who = known[rv.group(1)]
                for sg in last_dlg_segs:
                    if sg.source not in ("inciso", "manual", "guion"):
                        sg.speaker, sg.source, sg.conf, sg.guess = who, "voz descrita", CONF["voz descrita"], ""
                if recent:
                    recent[-1] = who
            last_dlg_segs = []
        if not dlg:
            for k, s, e in pcs:
                segs.append(Segment("narr", text[s:e], s, e, mood=detect_mood(text[s:e])))
            for nm in names_in(narr_txt):
                if nm in mentions:
                    mentions.remove(nm)
                mentions.append(nm)
                narr_mentions.append(nm)
            subs = subjects_in(narr_txt)
            if subs:
                current_subject = subs[-1]
            if len(narr_txt) > 350:
                recent = []
            prev_narr_only = narr_txt
            if segs:
                segs[-1].pause = "para"
            continue

        dlg_txt = " ".join(text[s:e] for _, s, e in dlg)
        speaker, hint, source = "", "", ""
        for k, s, e in pcs:
            if k == "narr":
                nm, h = explicit_speaker(text[s:e], rx)
                if nm and (nm.split()[0] in known or nm in known or not common_word(nm.split()[0])) \
                        and nm not in not_names:
                    speaker, source = aliases.get(nm, nm), "inciso"
                    break
                if nm:
                    h = "S"
                hint = hint or h
        gold = ""
        if _eval is not None and source == "inciso" and _eval["pick"]():
            # evaluación: se tapa la acotación ("—dijo Hestia") y se mira si el resto de reglas acierta
            tag_only = all(k == "dlg" or (len(text[s_:e_].strip(" —–-,.")) < 45 and rx.speech.search(text[s_:e_]))
                           for k, s_, e_ in pcs)
            if tag_only:
                gold, speaker, source, hint, narr_txt = speaker, "", "", "", ""
        voc = vocatives(dlg_txt, known)
        first_dlg = dlg[0][1]
        pre_narr = " ".join(text[s:e] for k, s, e in pcs if k == "narr" and e <= first_dlg)
        if gold:
            pre_narr = ""
        if not speaker and hint == "YO":
            speaker, source = "", "narrador (yo)"
        if not speaker and hint == "S":
            cands = subjects_in(pre_narr) or ([current_subject] if current_subject else []) or [protagonist]
            for nm in reversed(cands):
                if nm and nm not in voc:
                    speaker, source = nm, "sujeto"
                    break
            if not speaker and mentions and mentions[-1] not in voc and not mentions[-1].startswith("@"):
                speaker, source = mentions[-1], "párrafo previo"   # "—me dijo." tras nombrar a alguien
        if not speaker and not source and narr_txt and hint != "G":
            subs = subjects_in(narr_txt)
            nm = subs[0] if subs else ""
            if nm and nm not in voc:
                speaker, source = nm, "acción"
        if not speaker and not source and prev_narr_only and prev_narr_only.rstrip().endswith(":"):
            # "Dijo Hades:" / "Hades dijo con calma:" justo antes del diálogo
            last_s = re.split(r"(?<=[.!?…])\s+", prev_narr_only.strip())[-1]
            nm_, h_ = explicit_speaker(last_s, rx)
            nm_ = aliases.get(nm_, nm_)
            if nm_ and (nm_ in known.values() or nm_ in known) and known.get(nm_, nm_) not in voc:
                speaker, source = known.get(nm_, nm_), "anuncio"
        f1_hit = False
        if not speaker and not source and prev_narr_only and len(prev_narr_only) < 1500 and \
                hint not in ("G", "F", "M"):
            sents = re.split(r"(?<=[.!?…])\s+", prev_narr_only.strip())
            pm = PRON_START.match(sents[-1]) if sents else None
            if pm and not subjects_in(sents[-1]):
                # «Issei miró a Murayama. Ella vio el cambio…» → habla ella, no el último nombrado
                g_ = "F" if pm.group(1).lower() in ("ella", "she") else "M"
                pool_ = [n for n in dict.fromkeys(list(reversed(names_in(prev_narr_only))) + list(reversed(recent[-4:]))
                                                   + [current_subject])
                         if n and not n.startswith("@") and n not in voc and gender_of(n) == g_]
                if pool_:
                    speaker, source = pool_[0], "pronombre"
                    f1_hit = True
            for sent in reversed(sents[-5:]):
                if speaker:
                    break
                subs = subjects_in(sent)
                if subs and subs[0] not in voc:
                    speaker = subs[0]
                    source = "anuncio" if rx.speech.search(sent) else "párrafo previo"
                    break
        if hint == "G":
            speaker, source = "", "grupo"
        pron_unique = False
        if not speaker and not source and hint in ("F", "M"):
            pool = [nm for nm in dict.fromkeys(reversed(mentions[-8:] + recent[-4:]))
                    if not nm.startswith("@") and gender_of(nm) == hint and nm not in voc]
            pool2 = [nm for nm in pool if not recent or nm != recent[-1]]
            duo = [n for n in set(recent[-4:]) if not n.startswith("@")]
            if len(duo) == 2 and [gender_of(n) for n in duo].count(hint) == 1:
                # escena de dos con un hombre y una mujer: «dijo él» decide aunque la alternancia vaya desfasada
                pool2 = [n for n in duo if gender_of(n) == hint and n not in voc] or pool2
            elif len(recent) >= 2 and recent[-2] in pool2 and recent[-2] != recent[-1]:
                pool2.remove(recent[-2])
                pool2.insert(0, recent[-2])
            if pool2:
                speaker, source = pool2[0], "pronombre"
                pron_unique = len(pool) == 1
        if not speaker and not source and prev_narr_only and hint not in ("G", "YO"):
            sents = re.split(r"(?<=[.!?…])\s+", prev_narr_only.strip())
            last2 = [x for x in sents[-2:] if RE_CAP.match(x) and RE_CAP.match(x).group(0) not in STOP]
            m0 = RE_CAP.match(last2[-1]) if last2 else None
            if m0 and m0.group(0) in known and known[m0.group(0)] not in voc:
                speaker, source = known[m0.group(0)], "párrafo previo"
            elif m0 and m0.group(0) not in STOP and m0.group(0).lower() not in lower_words \
                    and len(m0.group(0)) > 2 and m0.group(0) not in not_names and not verb_like(m0.group(0)) \
                    and re.match(r"\s+(?:se\s+|le\s+|lo\s+|la\s+)?[a-záéíóúñ]{3,}", last2[-1][m0.end():]):
                speaker, source = m0.group(0), "nombre nuevo"
        if not speaker and not source and len(recent) >= 2 and recent[-1] != recent[-2]:
            speaker, source = recent[-2], "conversación"
            if speaker == "@yo":
                speaker, source = "", "narrador (yo)"
        if (not speaker and not source and prev_narr_only and current_subject
                and current_subject not in voc and hint not in ("G", "F", "M")):
            speaker, source = current_subject, "sujeto de la escena"
        last_sent = re.split(r"(?<=[.!?…])\s+", prev_narr_only.strip())[-1] if prev_narr_only else ""
        if (speaker and source in ("párrafo previo", "sujeto de la escena") and "?" in dlg_txt
                and SECOND_PERSON.search(dlg_txt) and not (voc and speaker not in voc)
                and not (len(last_sent) < 80 and subjects_in(last_sent)[:1] == [speaker])):
            others = [r for r in reversed(recent[-4:]) if r != speaker] or \
                     [n for n in subjects_in(prev_narr_only) if n != speaker]
            if others:
                speaker, source = others[0], "conversación"
        if not speaker and not source and not prev_narr_only and recent and not narr_txt:
            for nm in reversed(mentions):
                if nm != recent[-1] and nm not in voc:
                    speaker, source = nm, "réplica"
                    break
            if speaker == "@yo":
                speaker, source = "", "narrador (yo)"
        if speaker in voc and source in ("conversación", "pronombre", "párrafo previo", "acción",
                                         "sujeto de la escena", "réplica", "sujeto"):
            others = [r for r in reversed(recent) if r != speaker]
            speaker, source = (others[0], "conversación") if others else ("", "")
        if not speaker and not source and voc and len(set(recent[-4:])) == 2:
            others = [r for r in set(recent[-4:]) if r not in voc]
            if others:
                speaker, source = others[0], "conversación"
        if not speaker and not source and voc and recent:
            # le habla a alguien por su nombre: lo dice quien hablaba antes en la escena
            others = [r for r in reversed(recent[-4:]) if r not in voc and not r.startswith("@")]
            if others:
                speaker, source = others[0], "conversación"

        conf = CONF.get(source, 0.5)
        if source == "pronombre" and (pron_unique or f1_hit):
            conf = 0.8
        if source == "sujeto de la escena" and speaker == protagonist:
            conf = 0.58          # en historias con punto de vista fijo, el protagonista suele ser el sujeto
        if speaker and hint in ("F", "M") and gender_of(speaker) != hint:
            conf -= 0.25
        if speaker and source in WEAK_SOURCES and not anchors.get(speaker) and not person_sig.get(speaker) \
                and not is_role(speaker) and speaker not in script_names:
            conf -= 0.2      # nunca lleva "dijo X" ni actúa como persona: probablemente no es un personaje
        if source in calib and source not in ("manual", "guion"):
            ok_, bad_ = calib[source]
            conf = min(0.99, conf * (0.6 + 0.8 * (ok_ + 1) / (ok_ + bad_ + 2)))
        guess = ""
        cands = [c for c in dict.fromkeys(reversed(recent[-4:] + mentions[-6:])) if not c.startswith("@")]
        scene_speakers = [c for c in recent[-4:] if not c.startswith("@")]
        para_id = len(segs)
        if speaker and conf < threshold:
            guess, speaker = speaker, ""      # no está seguro: lo lee el narrador

        last_dlg_segs = []
        for k, s, e in pcs:
            t = text[s:e]
            if k == "dlg":
                seg = Segment("dlg", t, s, e, speaker=speaker, hint=hint, source=source, conf=conf,
                              guess=guess)
                seg.cands, seg.para, seg.voc, seg.scene = cands, para_id, sorted(voc), scene_speakers
                ov = _apply_key(seg, text, seen, seen_old, overrides)
                if ov is not None:
                    seg.speaker, seg.source, seg.conf, seg.guess = ov, "manual", 1.0, ""
                seg.emotion = detect_emotion(t, narr_txt)
                if gold:
                    seg.gold = gold
                last_dlg_segs.append(seg)
            else:
                seg = Segment("narr", t, s, e, mood="calma")
            segs.append(seg)
        if segs:
            segs[-1].pause = "para"
        final = next((sg.speaker or (sg.guess if sg.conf >= 0.45 and sg.source != "nombre nuevo" else "")
                      for sg in reversed(segs) if sg.kind == "dlg"), "")
        push_recent(final if final or source != "narrador (yo)" else "@yo")
        narr_mentions.extend(names_in(narr_txt))
        subs = subjects_in(narr_txt)
        if subs:
            current_subject = subs[-1]
        elif final and source in ("inciso", "sujeto", "acción"):
            current_subject = final
        prev_narr_only = ""

    # --- Estilo de cada personaje (se entrena solo con las líneas seguras)
    doc_model = {}
    for sg in segs:
        if sg.kind == "dlg" and sg.speaker and (sg.source in ("manual", "guion") or sg.conf >= 0.85):
            _model_add(doc_model, sg.speaker, style_tokens(sg.text, getattr(sg, "voc", ())),
                       3 if sg.source == "manual" else 1)
    info["style_model"] = _model_prune(doc_model)
    combined = {}
    for dname, dm in memory.get("style", {}).get(project or "_", {}).items():
        if dname == doc:
            continue
        for name, m in dm.items():
            c = combined.setdefault(name, {"n": 0, "w": {}})
            c["n"] += m["n"]
            for t, v in m["w"].items():
                c["w"][t] = c["w"].get(t, 0) + v
    for name, m in doc_model.items():
        c = combined.setdefault(name, {"n": 0, "w": {}})
        c["n"] += m["n"]
        for t, v in m["w"].items():
            c["w"][t] = c["w"].get(t, 0) + v
    combined = {n: m for n, m in combined.items() if m["n"] >= 5}
    info["style_chars"] = len(combined)
    if len(combined) >= 2:
        n_total = sum(m["n"] for m in combined.values())
        groups = {}
        for sg in segs:
            if sg.kind == "dlg" and sg.source not in STRONG_SOURCES and hasattr(sg, "para"):
                groups.setdefault(sg.para, []).append(sg)
        for pid in sorted(groups):
            g = groups[pid]
            toks = []
            for sg in g:
                toks += style_tokens(sg.text, getattr(sg, "voc", ()))
            cur = g[0].speaker or g[0].guess
            pool = [c for c in g[0].cands if c in combined]
            if cur and cur in combined and cur not in pool:
                pool.append(cur)
            dl = " ".join(sg.text for sg in g)
            pool = [c for c in pool if c not in getattr(g[0], "voc", ())
                    and not re.search(r"(?<!\w)" + re.escape(c.split()[0]) + r"(?!\w)", dl)]
            if len(pool) < 2 or len([t for t in toks if not t.startswith("@")]) < 2:
                continue
            seen_t = set()
            for c in pool:
                seen_t.update(combined[c]["w"])
            toks = [t for t in toks if t in seen_t]
            if not toks:
                continue
            scored = sorted(((style_score(combined[c], toks, n_total, len(combined)), c) for c in pool),
                            key=lambda x: (-x[0], x[1]))
            (b_sc, best), (s_sc, _) = scored[0], scored[1]
            margin = b_sc - s_sc
            for sg in g:
                if best == cur and margin >= 1.5:
                    sg.conf = min(0.9, sg.conf + 0.15)
                    if sg.guess and sg.conf >= threshold:
                        sg.speaker, sg.guess = sg.guess, ""
                        sg.source += " + estilo"
                        info["style_changes"] += 1
                elif best != cur and margin >= 4.0 and sg.conf < 0.63 and best in getattr(g[0], "scene", ()) \
                        and g[0].source in ("conversación", "réplica", "sujeto de la escena", "") \
                        and not (g[0].hint in ("F", "M") and gender_of(best) != g[0].hint):
                    nc = min(0.75, 0.45 + margin / 40)
                    sg.conf, sg.source = nc, "estilo aprendido"
                    if nc >= threshold:
                        sg.speaker, sg.guess = best, ""
                    else:
                        sg.speaker, sg.guess = "", best
                    info["style_changes"] += 1

    # --- Personajes
    counts = Counter(s.speaker for s in segs if s.kind == "dlg" and s.speaker)
    chars = {}
    for nm, c in counts.most_common():
        ev = {}
        if nm in mem_genders:
            g, sc = mem_genders[nm], 10
            ev["from"] = "memoria"
        else:
            sc = score_gender(text, nm, ev)
            g = "F" if sc > 0 else "M"
            ev["from"] = "texto"
        chars[nm] = Character(nm, c, g, min(1.0, abs(sc) / 6), guess_age(text, nm), evidence=ev)
    return segs, chars, info


def stats(segs):
    d = sum(1 for s in segs if s.kind == "dlg")
    k = sum(1 for s in segs if s.kind == "dlg" and s.speaker)
    return d, k
