#!/usr/bin/env python3
"""Build the SRMusic full Japanese WORD index from JMdict.

Outputs (in this directory):
  words.json            - flat {word: [kana, romaji]} — ONE default reading
                          per written word form (kanji words, kana-only words,
                          katakana loanwords, particles, conjunctions).
  words_alternates.json - {word: [alt_kana, ...]} — losing readings for words
                          that have more than one reading in JMdict
                          (written only if small, see below).
  words_conj.json       - (NOT a separate file) conjugated verb forms are
                          merged INTO words.json; see CONJUGATION below.
  JMdict_e.gz           - word-level source (kept locally, NOT pushed).

Why a word index? The per-kanji fallback (readings.json) reads kanji one by
one, which is wrong for whole words: 相変らず -> しょう+へん = "shouhen"
instead of あいかわらず. A word index gives the real reading of the real
word. App lookup order:
  lyric_overrides.json (SRMusic-JP-Index, word-level, wins) ->
  words.json (this file) -> readings.json (per-kanji fallback) ->
  Jisho/Google (last resort).

Priority (one default reading per word, best entry wins):
  ichi1 > news1 > news2 > nf01..nf50 (in order) > gai1 > ichi2/gai2 >
  spec1 > spec2 > untagged. Ties keep the first entry in JMdict order.
  (Slightly different from build_readings.py's pri_score, which was tuned
  for single-kanji defaults and does not rank news2 at all.)

What is EXCLUDED (never invented, never guessed):
  - name-only entries: every sense has a name <misc> tag (surname, place,
    company, organisation, product, work title, character, ...). ~7.3k.
  - search-only kanji/kana forms (ke_inf / re_inf "search-only ...").
  - readings marked re_nokanji when pairing with a kanji form.
  - keys containing Latin letters or digits (fullwidth or ASCII), or any
    character outside CJK / hiragana / katakana / ー / ・ / 々 / space / 、
    (placeholder symbols like ○, Latin loanwords written in romaji, etc.).
  - readings that are not pure kana after stripping spaces and "・".
  - entries with no usable reading.

CONJUGATION (merged into words.json):
  Dictionary forms only come from JMdict. For priority verbs (score >= 25,
  i.e. any priority tag) in the SAFE classes we also generate te / ta /
  nai / masu forms by deterministic rules, applied identically to the
  written form and its reading:
    v1  (ichidan)      る -> て/た/ない/ます        食べる -> 食べて
    v5r (godan -ru)    る -> って/った/らない/ります 帰る -> 帰って
    v5k (godan -ku)    く -> いて/いた/かない/きます 書く -> 書いて
    v5k-iku special    く -> って/った/かない/きます 行く -> 行って
    v5g (godan -gu)    ぐ -> いで/いだ/がない/ぎます 泳ぐ -> 泳いで
    v5s (godan -su)    す -> して/した/さない/します 話す -> 話して
    v5t (godan -tsu)   つ -> って/った/たない/ちます 待つ -> 待って
    v5n (godan -nu)    ぬ -> んで/んだ/なない/にます 死ぬ -> 死んで
    v5b (godan -bu)    ぶ -> んで/んだ/ばない/びます 飛ぶ -> 飛んで
    v5m (godan -mu)    む -> んで/んだ/まない/みます 読む -> 読んで
    vs  (suru verbs)   する -> して/した/しない/します 勉強する -> 勉強して
    vk  (kuru verbs)   る -> て/た/ない/ます          来る -> 来て
  DELIBERATELY SKIPPED (ambiguous from the written form alone):
    v5u (godan -u: 買う/会う/歌う/笑う/習う/払う/問う/恋う/沿う...) —
    te-form is って except a small closed set of long-vowel verbs
    (問うて, 恋うて, 沿うて, ...) whose full list is not in JMdict.
    Wrong forms are worse than missing forms; the dictionary forms ARE in
    the index, only their conjugations are missing (cover via overrides).
    Also skipped: v5aru, irregular-ru, v4/nidan/yodan (archaic), su-verb
    precursors, auxiliary verbs. A word tagged BOTH v1 and v5r (ambiguous)
    is skipped. A generated form that collides with an existing dictionary
    entry is never overwritten.
  KURU VERBS (vk): the written form conjugates regularly (来る -> 来て)
  but the READING does not (くる -> きて, not くて; こない, not くない).
  The vk rule therefore carries separate reading endings (くる -> きて /
  きた / こない / きます), which also works for compounds
  (持って来る -> 持って来て / もってきて).
  SURU NOUNS: JMdict usually has no separate entry for 勉強する — the
  noun 勉強 just carries "takes the aux. verb suru". For priority nouns
  with that tag we also generate the する dictionary form and its
  te/ta/nai/masu forms (勉強する/して/した/しない/します).

Romaji convention (same as build_readings.py / SRMusic-JP-Index):
  Hepburn, long vowels written doubled (koohii, yuube, toukyou).
  One deliberate deviation from textbook Hepburn: ん is always "n"
  (しんぶん -> shinbun, not shimbun), matching readings_romaji.json.
  Particle readings are literal here (は -> ha, を -> wo); the app applies
  the particle rule (は -> wa, へ -> e, を -> o) at romanisation time.

Sources: JMdict (EDRDG, CC BY-SA). See NOTICE.md.
"""
import gzip
import importlib.util
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
JMDICT_GZ = HERE / "JMdict_e.gz"
OVERRIDES_JSON = (
    Path.home() / "workspace" / "srmusic-kanji-index" / "lyric_overrides.json"
)
WORDS_JSON = HERE / "words.json"
ALTERNATES_JSON = HERE / "words_alternates.json"
INDEX_JSON = HERE / "words_index.json"
N_SHARDS = 8
MAX_SINGLE_FILE_MB = 12  # above this, shard (see shard_words())

# ---------------------------------------------------------------------------
# WORD_CORRECTIONS — forced defaults where JMdict's priority winner is a
# formal/literary/archaic reading while another reading is the everyday /
# lyric default. Each entry: word -> (kana, reason). The displaced reading
# is recorded as an alternate. Keep this table SMALL and justified.
# ---------------------------------------------------------------------------
WORD_CORRECTIONS = {
    # 昨夜: JMdict's ichi1 entry reads さくや; the everyday/lyric reading
    # is ゆうべ (also the SRMusic-JP-Index lyric override).
    "昨夜": ("ゆうべ", "JMdict ichi1 is さくや (formal/news); lyric default ゆうべ"),
    # 明日: JMdict's first reading is あした (common spoken); the lyric /
    # formal default is あす (also the lyric override; あした stays as an
    # alternate).
    "明日": ("あす", "JMdict first is あした (spoken); lyric default あす"),
    # 抱く: JMdict's first ichi1 entry reads いだく (marked formal/
    # literary); the modern lyric reading is だく (抱いて -> だいて).
    "抱く": ("だく", "JMdict first is いだく (formal/literary); lyric だく"),
    # 彼: two ichi1 entries tie (あれ "that" vs かれ "he/boyfriend");
    # file order picks あれ. In lyrics 彼 is almost always かれ.
    "彼": ("かれ", "ichi1 tie あれ/かれ, file order picks あれ; lyric かれ"),
}

# ---------------------------------------------------------------------------
# Reuse the kana -> romaji converter from build_readings.py (same Hepburn
# convention, incl. katakana handling). Import, do not duplicate.
# ---------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location(
    "build_readings", HERE / "build_readings.py"
)
_build_readings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build_readings)
kana_to_romaji = _build_readings.kana_to_romaji

ROMAJI_TESTS = [
    ("あいかわらず", "aikawarazu"),
    ("みおぼえ", "mioboe"),
    ("わたし", "watashi"),
    ("あい", "ai"),
    ("そして", "soshite"),
    ("しかし", "shikashi"),
    ("だから", "dakara"),
    ("コーヒー", "koohii"),
    ("たべる", "taberu"),
    ("いく", "iku"),
    ("きのう", "kinou"),
    ("まよなか", "mayonaka"),
    ("ふたり", "futari"),
    ("がっこう", "gakkou"),
    ("ちょっと", "chotto"),
    ("しんぶん", "shinbun"),  # ん is always "n" (repo convention)
    ("きゃ", "kya"),
    ("しゅ", "shu"),
    ("にゅう", "nyuu"),
    ("ぱん", "pan"),
    ("でんしゃ", "densha"),
    ("おはよう", "ohayou"),
    ("ありがとう", "arigatou"),
    ("きろぐらむ", "kiroguramu"),
    ("センチメートル", "senchimeetoru"),
    ("トン", "ton"),
]

# ---------------------------------------------------------------------------
# Priority scoring (see module docstring for the order).
# ---------------------------------------------------------------------------
def pri_score(tags):
    best = 0
    for t in tags:
        if t == "ichi1":
            best = max(best, 100)
        elif t == "news1":
            best = max(best, 90)
        elif t == "news2":
            best = max(best, 80)
        elif t.startswith("nf") and t[2:].isdigit():
            best = max(best, 70 - (int(t[2:]) - 1))
        elif t == "gai1":
            best = max(best, 35)
        elif t == "ichi2":
            best = max(best, 30)
        elif t == "gai2":
            best = max(best, 25)
        elif t == "spec1":
            best = max(best, 20)
        elif t == "spec2":
            best = max(best, 15)
    return best


CONJ_MIN_SCORE = 25  # any priority tag; see counts printed by the build

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
KANA_RE = re.compile(r"^[ぁ-んァ-ンー]+$")
ROMAJI_RE = re.compile(r"^[a-z ]+$")

# A key must contain at least one Japanese character and nothing outside
# CJK / hiragana / katakana / ー / ・ / 々 / space / 、 .
def _is_japanese_char(ch):
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x20000 <= o <= 0x2A6DF
        or 0x3041 <= o <= 0x3096
        or 0x30A1 <= o <= 0x30FA
    )


def valid_key(key):
    if not key or not key.strip():
        return False
    has_jp = False
    for ch in key:
        if _is_japanese_char(ch):
            has_jp = True
        elif ch in "ー・々 、":
            continue
        else:
            return False
    return has_jp


def clean_reading(reb):
    """Normalise one JMdict reading; return None if unusable."""
    if not reb:
        return None
    # Halfwidth katakana (ﾀﾋ) -> fullwidth; strip separators/spaces.
    reb = unicodedata.normalize("NFKC", reb).strip()
    reb = reb.replace("・", "").replace(" ", "").replace("　", "")
    if not reb or not KANA_RE.match(reb):
        return None
    return reb


# <misc> values that mark a sense as a proper name. An entry is name-only
# when EVERY sense has at least one of these.
NAME_MISC = {
    "surname", "family or surname",
    "given name or forename, gender not specified",
    "full name of a particular person",
    "place name", "company name", "organization name", "product name",
    "work of art, literature, music, etc. name", "character", "deity",
    "ship name", "event", "document", "service", "group", "object",
    "mythology", "fiction", "legend", "religion", "unclassified name",
    "creature",
}

# Verb classes (JMdict <pos> text) -> rule name.
# A rule is (w_ending, w_te, w_ta, w_nai, w_masu,
#             r_ending, r_te, r_ta, r_nai, r_masu):
# w_* transforms the written form, r_* the reading. They are identical
# except for vk (来る: written 来て, reading きて).
CONJ_RULES = {
    "v1": ("る", "て", "た", "ない", "ます",
           "る", "て", "た", "ない", "ます"),
    "v5r": ("る", "って", "った", "らない", "ります",
            "る", "って", "った", "らない", "ります"),
    "v5k": ("く", "いて", "いた", "かない", "きます",
            "く", "いて", "いた", "かない", "きます"),
    "v5k-iku": ("く", "って", "った", "かない", "きます",
                "く", "って", "った", "かない", "きます"),
    "v5g": ("ぐ", "いで", "いだ", "がない", "ぎます",
            "ぐ", "いで", "いだ", "がない", "ぎます"),
    "v5s": ("す", "して", "した", "さない", "します",
            "す", "して", "した", "さない", "します"),
    "v5t": ("つ", "って", "った", "たない", "ちます",
            "つ", "って", "った", "たない", "ちます"),
    "v5n": ("ぬ", "んで", "んだ", "なない", "にます",
            "ぬ", "んで", "んだ", "なない", "にます"),
    "v5b": ("ぶ", "んで", "んだ", "ばない", "びます",
            "ぶ", "んで", "んだ", "ばない", "びます"),
    "v5m": ("む", "んで", "んだ", "まない", "みます",
            "む", "んで", "んだ", "まない", "みます"),
    "vs": ("する", "して", "した", "しない", "します",
           "する", "して", "した", "しない", "します"),
    "vk": ("る", "て", "た", "ない", "ます",
           "くる", "きて", "きた", "こない", "きます"),
}
POS_TO_RULE = {
    "Ichidan verb": "v1",
    "Ichidan verb - kureru special class": "v1",
    "Godan verb with 'ru' ending": "v5r",
    "Godan verb with 'ku' ending": "v5k",
    "Godan verb - Iku/Yuku special class": "v5k-iku",
    "Godan verb with 'gu' ending": "v5g",
    "Godan verb with 'su' ending": "v5s",
    "Godan verb with 'tsu' ending": "v5t",
    "Godan verb with 'nu' ending": "v5n",
    "Godan verb with 'bu' ending": "v5b",
    "Godan verb with 'mu' ending": "v5m",
    "suru verb - included": "vs",
    "suru verb - special class": "vs",
    "Kuru verb - special class": "vk",
}


def pick_rule(verb_rules, keb, reb):
    """Return the one applicable conjugation rule, or None if ambiguous."""
    if not verb_rules:
        return None
    # A verb ending in 行く/往く always takes the iku rule (って), even if
    # it was tagged plain v5k (compounds like 連れて行く).
    if keb.endswith("行く") or keb.endswith("往く"):
        if "v5k" in verb_rules or "v5k-iku" in verb_rules:
            return CONJ_RULES["v5k-iku"]
    cands = []
    for rule in sorted(set(verb_rules)):
        w_ending = CONJ_RULES[rule][0]
        r_ending = CONJ_RULES[rule][5]
        if keb.endswith(w_ending) and reb.endswith(r_ending):
            cands.append(rule)
    if len(cands) == 1:
        return CONJ_RULES[cands[0]]
    return None  # ambiguous (e.g. tagged both v1 and v5r) or no match


def conjugate(keb, reb, rule):
    """Return [(form, form_reading)] for te/ta/nai/masu, or [] if unsafe."""
    (w_end, w_te, w_ta, w_nai, w_masu,
     r_end, r_te, r_ta, r_nai, r_masu) = rule
    if not keb.endswith(w_end) or not reb.endswith(r_end):
        return []
    if " " in keb or "、" in keb:
        return []
    stem_w, stem_r = keb[: -len(w_end)], reb[: -len(r_end)]
    out = []
    for w_suf, r_suf in ((w_te, r_te), (w_ta, r_ta), (w_nai, r_nai),
                         (w_masu, r_masu)):
        fw, fr = stem_w + w_suf, stem_r + r_suf
        if not valid_key(fw) or not KANA_RE.match(fr):
            return []
        out.append((fw, fr))
    return out


# ---------------------------------------------------------------------------
# JMdict parsing
# ---------------------------------------------------------------------------
def parse_jmdict():
    """Return (words, alternates, verb_info, stats).

    words: key -> (score, kana)  (best entry wins)
    alternates: key -> [(score, kana)]  (losing readings, best first)
    verb_info: key -> (score, [rule names]) for selected verb words
    """
    words = {}
    alternates = {}
    verb_info = {}
    suru_nouns = {}  # noun key -> (score, kana) for "takes suru" nouns
    stats = {
        "entries": 0, "name_only": 0, "kana_only_entries": 0,
        "keys_added": 0, "keys_replaced": 0,
        "skip_no_reading": 0, "skip_bad_key": 0, "skip_bad_reading": 0,
        "skip_search_only_keb": 0, "skip_search_only_reb": 0,
        "skip_re_nokanji_all": 0,
        "multi_reading_words": 0,
        "kana_form_keys": 0, "kana_form_keys_rare_keb": 0,
        "skipped_verb_classes": 0,
    }
    # Verb-ish <pos> values NOT in POS_TO_RULE (deliberately not conjugated).
    SKIPPED_VERB_POS = {
        "Godan verb with 'u' ending",
        "Godan verb with 'u' ending (special class)",
        "Godan verb - -aru special class",
        "Godan verb with 'ru' ending (irregular verb)",
        "Nidan verb (lower class) with 'dzu' ending (archaic)",
        "Nidan verb (lower class) with 'gu' ending (archaic)",
        "Nidan verb (lower class) with 'hu/fu' ending (archaic)",
        "Nidan verb (lower class) with 'ku' ending (archaic)",
        "Nidan verb (lower class) with 'mu' ending (archaic)",
        "Nidan verb (lower class) with 'nu' ending (archaic)",
        "Nidan verb (lower class) with 'ru' ending (archaic)",
        "Nidan verb (lower class) with 'su' ending (archaic)",
        "Nidan verb (lower class) with 'tsu' ending (archaic)",
        "Nidan verb (lower class) with 'u' ending and 'we' conjugation (archaic)",
        "Nidan verb (lower class) with 'yu' ending (archaic)",
        "Nidan verb (lower class) with 'zu' ending (archaic)",
        "Nidan verb (upper class) with 'bu' ending (archaic)",
        "Nidan verb (upper class) with 'gu' ending (archaic)",
        "Nidan verb (upper class) with 'hu/fu' ending (archaic)",
        "Nidan verb (upper class) with 'ku' ending (archaic)",
        "Nidan verb (upper class) with 'ru' ending (archaic)",
        "Nidan verb (upper class) with 'tsu' ending (archaic)",
        "Nidan verb (upper class) with 'yu' ending (archaic)",
        "Nidan verb with 'u' ending (archaic)",
        "Yodan verb with 'bu' ending (archaic)",
        "Yodan verb with 'gu' ending (archaic)",
        "Yodan verb with 'hu/fu' ending (archaic)",
        "Yodan verb with 'ku' ending (archaic)",
        "Yodan verb with 'mu' ending (archaic)",
        "Yodan verb with 'ru' ending (archaic)",
        "Yodan verb with 'su' ending (archaic)",
        "Yodan verb with 'tsu' ending (archaic)",
        "auxiliary verb",
        "su verb - precursor to the modern suru",
        "irregular nu verb",
        "irregular ru verb, plain form ends with -ri",
        "verb unspecified",
    }

    def offer(key, kana, score, rules):
        nonlocal stats
        if not valid_key(key):
            stats["skip_bad_key"] += 1
            return
        cur = words.get(key)
        if cur is None:
            words[key] = (score, kana)
            stats["keys_added"] += 1
            if rules:
                verb_info[key] = (score, rules)
        elif score > cur[0]:
            # old winner becomes an alternate
            alternates.setdefault(key, []).append((cur[0], cur[1]))
            words[key] = (score, kana)
            stats["keys_replaced"] += 1
            if rules:
                verb_info[key] = (score, rules)
            elif key in verb_info:
                del verb_info[key]
        elif kana != cur[1]:
            alternates.setdefault(key, []).append((score, kana))

    for _ev, entry in ET.iterparse(gzip.open(JMDICT_GZ, "rb"), events=("end",)):
        if entry.tag != "entry":
            continue
        stats["entries"] += 1
        senses = entry.findall("sense")
        # Name-only entries are excluded wholesale.
        if senses and all(
            any(m.text in NAME_MISC for m in s.findall("misc"))
            for s in senses
        ):
            stats["name_only"] += 1
            entry.clear()
            continue
        # Verb rules for this entry (from any sense).
        rules = set()
        suru_pos = False
        for s in senses:
            for p in s.findall("pos"):
                if p.text in POS_TO_RULE:
                    rules.add(POS_TO_RULE[p.text])
                elif p.text in SKIPPED_VERB_POS:
                    stats["skipped_verb_classes"] += 1
                if p.text == "noun or participle which takes the aux. verb suru":
                    suru_pos = True
        rules = sorted(rules)
        # "Usually written using kana alone" -> the kana form is a real
        # written form in lyrics (そして, ありがとう, コーヒー, ...), so it
        # gets its own key in addition to the kanji form(s).
        kana_alone = any(
            m.text == "word usually written using kana alone"
            for s in senses for m in s.findall("misc")
        )

        # Kanji elements (skip search-only forms).
        kebs = []
        for ke in entry.findall("k_ele"):
            keb_el = ke.find("keb")
            if keb_el is None or not keb_el.text:
                continue
            keb = keb_el.text.strip()
            infs = [i.text for i in ke.findall("ke_inf")]
            if "search-only kanji form" in infs:
                stats["skip_search_only_keb"] += 1
                continue
            pris = [p.text for p in ke.findall("ke_pri") if p.text]
            kebs.append((keb, pris))
        # Reading elements (skip search-only forms; note re_nokanji/restr).
        rebs = []
        for re in entry.findall("r_ele"):
            reb_el = re.find("reb")
            if reb_el is None or not reb_el.text:
                continue
            infs = [i.text for i in re.findall("re_inf")]
            if "search-only kana form" in infs:
                stats["skip_search_only_reb"] += 1
                continue
            kana = clean_reading(reb_el.text)
            if kana is None:
                stats["skip_bad_reading"] += 1
                continue
            nokanji = re.find("re_nokanji") is not None
            restr = [r.text for r in re.findall("re_restr") if r.text]
            pris = [p.text for p in re.findall("re_pri") if p.text]
            rebs.append((kana, nokanji, restr, pris))
        if not rebs:
            stats["skip_no_reading"] += 1
            entry.clear()
            continue

        if not kebs:
            # Kana-only entry: key is the reading itself.
            stats["kana_only_entries"] += 1
            kana, _nok, _restr, pris = rebs[0]
            score = pri_score(pris)
            offer(kana, kana, score, rules)
            if suru_pos:
                cur = suru_nouns.get(kana)
                if cur is None or score > cur[0]:
                    suru_nouns[kana] = (score, kana)
            entry.clear()
            continue

        for keb, ke_pris in kebs:
            # First reading usable with this kanji form.
            chosen = None
            for kana, nokanji, restr, re_pris in rebs:
                if nokanji:
                    continue
                if restr and keb not in restr:
                    continue
                chosen = (kana, re_pris)
                break
            if chosen is None:
                stats["skip_re_nokanji_all"] += 1
                continue
            kana, re_pris = chosen
            score = pri_score(ke_pris + re_pris)
            offer(keb, kana, score, rules)
            if suru_pos:
                cur = suru_nouns.get(keb)
                if cur is None or score > cur[0]:
                    suru_nouns[keb] = (score, kana)
        # Kana-form key: the word is usually written in kana alone, or
        # every surviving kanji form is "rarely used" (e.g. 迚も -> とても).
        # The kana form is what lyrics actually contain.
        if kebs and (kana_alone or all(
            "rarely used kanji form" in
            [i.text for ke in entry.findall("k_ele")
             for i in ke.findall("ke_inf")
             if ke.find("keb") is not None
             and ke.find("keb").text == keb]
            for keb, _p in kebs
        )):
            kana0 = rebs[0][0]
            before = len(words)
            offer(kana0, kana0, pri_score(
                [p for _k, ps in kebs for p in ps] +
                [p for _kn, _nk, _rs, ps in rebs for p in ps]), rules)
            if len(words) > before:
                stats["kana_form_keys"] += 1
                if not kana_alone:
                    stats["kana_form_keys_rare_keb"] += 1
        entry.clear()

    stats["multi_reading_words"] = len(alternates)
    return words, alternates, verb_info, suru_nouns, stats


# ---------------------------------------------------------------------------
# Conjugation generation
# ---------------------------------------------------------------------------
def generate_conjugations(words, verb_info, suru_nouns):
    """Generate te/ta/nai/masu forms for priority verbs, plus する forms
    for priority suru-nouns. Returns (added, stats)."""
    added = {}
    stats = {
        "verbs_considered": 0, "verbs_generated": 0, "forms_added": 0,
        "skip_low_score": 0, "skip_ambiguous": 0, "skip_no_match": 0,
        "collisions": 0, "dupes": 0,
        "suru_nouns_considered": 0, "suru_nouns_generated": 0,
        "suru_forms_added": 0,
        "collision_samples": [],
    }

    def add_form(fw, fr):
        cur = words.get(fw)
        if cur is not None:
            if cur[1] == fr:
                stats["dupes"] += 1
            else:
                stats["collisions"] += 1
                if len(stats["collision_samples"]) < 10:
                    stats["collision_samples"].append((fw, fr, cur[1]))
            return False
        if fw in added:
            if added[fw][1] != fr:
                stats["collisions"] += 1
                if len(stats["collision_samples"]) < 10:
                    stats["collision_samples"].append((fw, fr, added[fw][1]))
            else:
                stats["dupes"] += 1
            return False
        added[fw] = (0, fr)
        return True

    for key in sorted(verb_info):
        score, rules = verb_info[key]
        stats["verbs_considered"] += 1
        if score < CONJ_MIN_SCORE:
            stats["skip_low_score"] += 1
            continue
        kana = words[key][1]
        rule = pick_rule(rules, key, kana)
        if rule is None:
            n_match = len([
                r for r in set(rules)
                if key.endswith(CONJ_RULES[r][0])
                and kana.endswith(CONJ_RULES[r][5])])
            if n_match > 1:
                stats["skip_ambiguous"] += 1
            else:
                stats["skip_no_match"] += 1
            continue
        forms = conjugate(key, kana, rule)
        if not forms:
            stats["skip_no_match"] += 1
            continue
        stats["verbs_generated"] += 1
        for fw, fr in forms:
            if add_form(fw, fr):
                stats["forms_added"] += 1

    # Suru nouns: 勉強 -> 勉強する/して/した/しない/します.
    for noun in sorted(suru_nouns):
        score, kana = suru_nouns[noun]
        stats["suru_nouns_considered"] += 1
        if score < CONJ_MIN_SCORE:
            stats["skip_low_score"] += 1
            continue
        if words.get(noun, (None, None))[1] != kana:
            continue  # noun is not selected with this reading
        if " " in noun or "、" in noun:
            continue
        stats["suru_nouns_generated"] += 1
        for w_suf, r_suf in (("する", "する"), ("して", "して"),
                             ("した", "した"), ("しない", "しない"),
                             ("します", "します")):
            fw, fr = noun + w_suf, kana + r_suf
            if not valid_key(fw) or not KANA_RE.match(fr):
                continue
            if add_form(fw, fr):
                stats["suru_forms_added"] += 1

    for fw, (_score, fr) in added.items():
        words[fw] = (_score, fr)
    return added, stats


def shard_words(out):
    """Split {word: [kana, romaji]} into N_SHARDS files by the codepoint
    of each word's first character, balanced by byte size, and write a
    manifest (words_index.json) mapping first-char codepoint ranges to
    shard files. App lookup: cp = word.codePointAt(0); find the shard
    with from_cp <= cp <= to_cp; fetch it once and cache; look the word
    up inside. Returns the list of shard filenames.
    """
    keys = sorted(out)
    # Per-key byte size in the compact dump (key + value + overhead).
    def key_bytes(k):
        v = out[k]
        return (len(k.encode("utf-8")) + len(v[0].encode("utf-8"))
                + len(v[1].encode("utf-8")) + 12)

    total = sum(key_bytes(k) for k in keys)
    target = total / N_SHARDS
    shards = []  # list of (keys, bytes)
    cur, cur_bytes = [], 0
    for k in keys:
        b = key_bytes(k)
        # Cut only at a first-character boundary, and only once the
        # current shard is at least at target size (last shard takes
        # the remainder).
        if (cur and cur_bytes >= target and len(shards) < N_SHARDS - 1
                and k[0] != cur[-1][0]):
            shards.append((cur, cur_bytes))
            cur, cur_bytes = [], 0
        cur.append(k)
        cur_bytes += b
    if cur:
        shards.append((cur, cur_bytes))
    assert len(shards) == N_SHARDS, \
        f"expected {N_SHARDS} shards, got {len(shards)}"

    manifest = {"version": 1, "total": len(out),
                "format": "word -> [kana, romaji]",
                "lookup": "cp = word.codePointAt(0); shard with "
                          "from_cp <= cp <= to_cp",
                "shards": []}
    for i, (ks, _b) in enumerate(shards):
        fname = f"words-{i:02d}.json"
        data = {k: out[k] for k in ks}
        write_json(HERE / fname, data)
        size = (HERE / fname).stat().st_size
        first_cp, last_cp = ord(ks[0][0]), ord(ks[-1][0])
        manifest["shards"].append({
            "file": fname, "from_cp": first_cp, "to_cp": last_cp,
            "from": ks[0][0], "to": ks[-1][0],
            "entries": len(ks), "bytes": size,
        })
        print(f"  {fname}: {len(ks)} entries, {size/1024/1024:.2f} MB, "
              f"first char U+{first_cp:04X}..U+{last_cp:04X}")
    write_json(INDEX_JSON, manifest)
    print(f"words_index.json: {INDEX_JSON.stat().st_size} bytes")
    return [s["file"] for s in manifest["shards"]]


# ---------------------------------------------------------------------------
# Writing / verification
# ---------------------------------------------------------------------------
def write_json(path, data):
    # One entry per line: `"key": [kana, romaji],` then a newline — the same
    # style as readings.json. A single multi-MB line is slow to load/render
    # (GitHub, editors, and any line-based reader choke on it) and useless
    # for diffs; one entry per line costs ~1 byte per entry.
    with open(path, "w", encoding="utf-8") as f:
        items = sorted(data.items())
        f.write("{\n")
        for i, (k, v) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            f.write(json.dumps(k, ensure_ascii=False) + ": "
                    + json.dumps(v, ensure_ascii=False) + comma + "\n")
        f.write("}\n")


def main():
    for kana, want in ROMAJI_TESTS:
        got = kana_to_romaji(kana)
        assert got == want, f"romaji: {kana} -> {got}, want {want}"
    print(f"romaji converter tests OK: {len(ROMAJI_TESTS)}")

    words, alternates, verb_info, suru_nouns, stats = parse_jmdict()
    print(f"JMdict entries scanned: {stats['entries']}")
    print(f"  name-only entries excluded: {stats['name_only']}")
    print(f"  kana-only entries: {stats['kana_only_entries']}")
    print(f"  keys added: {stats['keys_added']}, replaced by higher score: "
          f"{stats['keys_replaced']}")
    print(f"  skipped: no reading {stats['skip_no_reading']}, bad key "
          f"{stats['skip_bad_key']}, bad reading {stats['skip_bad_reading']}, "
          f"search-only keb {stats['skip_search_only_keb']}, search-only reb "
          f"{stats['skip_search_only_reb']}, no usable reading for keb "
          f"{stats['skip_re_nokanji_all']}")
    print(f"  words with multiple readings: {stats['multi_reading_words']}")
    print(f"  kana-form keys added: {stats['kana_form_keys']} "
          f"(of which rare-keb only: {stats['kana_form_keys_rare_keb']})")
    print(f"  skipped verb-class senses (v5u, archaic, aux, ...): "
          f"{stats['skipped_verb_classes']}")
    dict_words = len(words)

    # Word corrections: force the lyric/everyday default where JMdict's
    # priority winner is a formal/literary reading. The displaced reading
    # becomes an alternate. Applied BEFORE conjugation so generated forms
    # use the corrected reading (抱く -> だく gives 抱いて -> だいて).
    for word, (kana, why) in WORD_CORRECTIONS.items():
        if word not in words:
            print(f"  WARNING: correction target missing: {word}")
            continue
        old_score, old_kana = words[word]
        if old_kana != kana:
            alternates.setdefault(word, []).append((old_score, old_kana))
            words[word] = (old_score, kana)
            print(f"  corrected: {word}: {old_kana} -> {kana} ({why})")

    conj_added, cstats = generate_conjugations(words, verb_info, suru_nouns)
    print(f"conjugation: verbs considered {cstats['verbs_considered']}, "
          f"generated {cstats['verbs_generated']}, forms added "
          f"{cstats['forms_added']}")
    print(f"  suru nouns: considered {cstats['suru_nouns_considered']}, "
          f"generated {cstats['suru_nouns_generated']}, forms added "
          f"{cstats['suru_forms_added']}")
    print(f"  skipped: low score {cstats['skip_low_score']}, ambiguous "
          f"{cstats['skip_ambiguous']}, no match {cstats['skip_no_match']}, "
          f"collisions {cstats['collisions']}, dupes {cstats['dupes']}")
    for fw, fr, existing in cstats.get("collision_samples", []):
        print(f"  collision sample: {fw}: generated {fr}, kept {existing}")

    # Final values: [kana, romaji].
    out = {}
    for k, (_score, kana) in words.items():
        roma = kana_to_romaji(kana)
        assert "?" not in roma, f"romaji failed for {k}: {kana} -> {roma}"
        assert KANA_RE.match(kana), f"bad kana for {k}: {kana}"
        assert ROMAJI_RE.match(roma), f"bad romaji for {k}: {roma}"
        out[k] = [kana, roma]

    # Layout decision: single file if small enough, else shards by first
    # character codepoint (see shard_words()).
    total_bytes = len(json.dumps(out, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
    if total_bytes <= MAX_SINGLE_FILE_MB * 1024 * 1024:
        write_json(WORDS_JSON, out)
        print(f"words.json: {len(out)} entries "
              f"({dict_words} dictionary + {len(conj_added)} conjugated), "
              f"{total_bytes/1024/1024:.2f} MB (single file)")
        shard_files = [WORDS_JSON.name]
    else:
        shard_files = shard_words(out)
        print(f"words.json NOT written: {total_bytes/1024/1024:.2f} MB > "
              f"{MAX_SINGLE_FILE_MB} MB -> {len(shard_files)} shards")
        if WORDS_JSON.exists():  # stale single file from an earlier run
            WORDS_JSON.unlink()
            print("  removed stale words.json (sharded layout)")
    print(f"  total entries: {len(out)} "
          f"({dict_words} dictionary + {len(conj_added)} conjugated)")

    # Alternates: kana only, best first, max 3 — only if small.
    alt_out = {}
    for k, lst in alternates.items():
        seen = []
        for _s, kana in sorted(lst, key=lambda x: -x[0]):
            if kana != out[k][0] and kana not in seen:
                seen.append(kana)
        if seen:
            alt_out[k] = seen[:3]
    alt_size = len(json.dumps(alt_out, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))
    if alt_out and alt_size <= 0.10 * total_bytes:
        write_json(ALTERNATES_JSON, alt_out)
        print(f"words_alternates.json: {len(alt_out)} words, "
              f"{alt_size/1024:.1f} KB ({100*alt_size/total_bytes:.1f}% of main)")
    else:
        print(f"alternates NOT written: {len(alt_out)} words would be "
              f"{alt_size/1024:.1f} KB ({100*alt_size/total_bytes:.1f}% of main)")

    # ---- verification: spot-checks -------------------------------------
    checks = {
        "相変わらず": ("あいかわらず", "aikawarazu"),
        "相変らず": ("あいかわらず", "aikawarazu"),
        "見覚え": ("みおぼえ", "mioboe"),
        "私": ("わたし", "watashi"),
        "愛": ("あい", "ai"),
        "そして": ("そして", "soshite"),
        "しかし": ("しかし", "shikashi"),
        "だから": ("だから", "dakara"),
        "コーヒー": ("コーヒー", "koohii"),
        "食べる": ("たべる", "taberu"),
        "行く": ("いく", "iku"),
        "昨日": ("きのう", "kinou"),
        "真夜中": ("まよなか", "mayonaka"),
        "二人": ("ふたり", "futari"),
        "昨夜": ("ゆうべ", "yuube"),
        "二人": ("ふたり", "futari"),
        "言葉": ("ことば", "kotoba"),
        "本当": ("ほんとう", "hontou"),
        "大事": ("だいじ", "daiji"),
        "忘れる": ("わすれる", "wasureru"),
        "帰る": ("かえる", "kaeru"),
        "歌う": ("うたう", "utau"),
        "笑う": ("わらう", "warau"),
        "です": ("です", "desu"),
        "ます": ("ます", "masu"),
        "は": ("は", "ha"),
        "を": ("を", "wo"),
        "が": ("が", "ga"),
        "に": ("に", "ni"),
        "の": ("の", "no"),
        "と": ("と", "to"),
        "も": ("も", "mo"),
        "でも": ("でも", "demo"),
        "まだ": ("まだ", "mada"),
        "もう": ("もう", "mou"),
        "とても": ("とても", "totemo"),
        "すごく": ("すごく", "sugoku"),
        "ありがとう": ("ありがとう", "arigatou"),
        "おはよう": ("おはよう", "ohayou"),
        "こんにちは": ("こんにちは", "konnichiha"),
        "さようなら": ("さようなら", "sayounara"),
    }
    bad = 0
    for w, want in checks.items():
        got = tuple(out.get(w, [None, None]))
        if got != want:
            print(f"  SPOT-CHECK FAIL: {w}: got {got}, want {want}")
            bad += 1
    print(f"spot-checks: {len(checks)-bad}/{len(checks)} OK")
    assert bad == 0, "spot-check failures"

    # Conjugation spot-checks (expected forms).
    conj_checks = {
        "食べて": ("たべて", "tabete"),
        "食べた": ("たべた", "tabeta"),
        "食べない": ("たべない", "tabenai"),
        "食べます": ("たべます", "tabemasu"),
        "行って": ("いって", "itte"),
        "行った": ("いった", "itta"),
        "行かない": ("いかない", "ikanai"),
        "行きます": ("いきます", "ikimasu"),
        "書いて": ("かいて", "kaite"),
        "書かない": ("かかない", "kakanai"),
        "話して": ("はなして", "hanashite"),
        "待って": ("まって", "matte"),
        "死んで": ("しんで", "shinde"),
        "読んで": ("よんで", "yonde"),
        "飛んで": ("とんで", "tonde"),
        "泳いで": ("およいで", "oyoide"),
        "帰って": ("かえって", "kaette"),
        "来て": ("きて", "kite"),
        "来ない": ("こない", "konai"),
        "来ます": ("きます", "kimasu"),
        "勉強する": ("べんきょうする", "benkyousuru"),
        "勉強して": ("べんきょうして", "benkyoushite"),
        "勉強した": ("べんきょうした", "benkyoushita"),
        "勉強しない": ("べんきょうしない", "benkyoushinai"),
        "勉強します": ("べんきょうします", "benkyoushimasu"),
        "愛して": ("あいして", "aishite"),
        "忘れて": ("わすれて", "wasurete"),
        "持って来て": ("もってきて", "mottekite"),
    }
    bad = 0
    for w, want in conj_checks.items():
        got = tuple(out.get(w, [None, None]))
        if got != want:
            print(f"  CONJ-CHECK FAIL: {w}: got {got}, want {want}")
            bad += 1
    print(f"conjugation checks: {len(conj_checks)-bad}/{len(conj_checks)} OK")
    assert bad == 0, "conjugation check failures"

    # ---- verification: lyric_overrides overlap -------------------------
    if OVERRIDES_JSON.exists():
        ov = json.loads(OVERRIDES_JSON.read_text(encoding="utf-8"))
        mism = []
        missing = []
        for e in ov:
            w = e["word"]
            if w not in out:
                missing.append(w)
            elif out[w][0] != e["reading"] or out[w][1] != e["romaji"]:
                mism.append((w, tuple(out[w]), (e["reading"], e["romaji"])))
        print(f"override overlap: {len(ov)} overrides, "
              f"{len(ov)-len(missing)-len(mism)} match, "
              f"{len(missing)} missing from words.json, "
              f"{len(mism)} mismatched")
        for w in missing:
            print(f"  MISSING: {w}")
        for w, got, want in mism:
            print(f"  MISMATCH: {w}: words.json {got}, override {want}")
    else:
        print("override overlap: lyric_overrides.json not found, skipped")

    # ---- verification: reload + format ---------------------------------
    # Reload from the files actually written (single file or shards) and
    # compare against the in-memory index.
    reloaded = {}
    if shard_files == [WORDS_JSON.name]:
        reloaded = json.loads(WORDS_JSON.read_text(encoding="utf-8"))
    else:
        manifest = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
        assert len(manifest["shards"]) == N_SHARDS
        covered = 0
        for s in manifest["shards"]:
            part = json.loads((HERE / s["file"]).read_text(encoding="utf-8"))
            assert len(part) == s["entries"], f"shard count: {s['file']}"
            assert all(s["from_cp"] <= ord(k[0]) <= s["to_cp"]
                       for k in part), f"shard range: {s['file']}"
            assert not (set(part) & set(reloaded)), \
                f"overlapping keys in {s['file']}"
            reloaded.update(part)
            covered += s["bytes"]
        print(f"shards reloaded: {len(reloaded)} entries, "
              f"{covered/1024/1024:.2f} MB total")
    assert reloaded == out, "reload mismatch"
    n_space = sum(1 for v in out.values() if " " in v[1])
    print(f"reload OK; entries with a space in romaji (phrases): {n_space}")
    print("BUILD OK")


if __name__ == "__main__":
    main()
