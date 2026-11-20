#!/usr/bin/env python3
"""Build the SRMusic flat kanji default-reading index from KANJIDIC2 + JMdict.

Outputs (in this directory):
  readings.json        - flat {kanji: hiragana} — ONE default reading per kanji
  readings_romaji.json - same keys, Hepburn romaji values
  JMdict_e.gz          - word-level source (kept locally, NOT pushed to GitHub)

Why flat, one reading per kanji?
  The app fetches one small file and looks a kanji up in O(1) instead of
  scanning arrays of readings. It is the per-kanji FALLBACK only — the app
  must still apply word-level lyric overrides first
  (SRMusic-JP-Index lyric_overrides.json: 昨夜→ゆうべ, 二人→ふたり, ...),
  then this file, then Jisho/Google as a last resort.

Default-reading algorithm (in order — naive "first kun reading" is WRONG
and must not be used; it reproduces the watakushi bug):
  1. MANUAL_CORRECTIONS below — lyric/common-sense defaults, win over all.
  2. JMdict single-kanji word entries: entries whose kanji element is exactly
     that one kanji, preferring priority tags (ichi1, news1, nf01-nf24,
     spec1/2). First reading (reb) of the best-scoring entry.
  3. KANJIDIC2 fallback: first kun reading (okurigana '.' and bound '-'
     markers stripped), else first on reading converted to hiragana.
  4. Skip kanji with no usable reading rather than inventing one.

Romaji convention (same as SRMusic-JP-Index build_index.py, reused here):
  Hepburn, long vowels written doubled (not macron): yuube, kyou, toukyou.

Sources: KANJIDIC2 + JMdict, both EDRDG, CC BY-SA. See NOTICE.md.
"""
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
KANJIDIC_GZ = Path.home() / "workspace" / "srmusic-kanji-index" / "kanjidic2.xml.gz"
JMDICT_GZ = HERE / "JMdict_e.gz"
READINGS_JSON = HERE / "readings.json"
READINGS_ROMAJI_JSON = HERE / "readings_romaji.json"

# ---------------------------------------------------------------------------
# 1. MANUAL CORRECTIONS — must win over everything.
#    Each entry: kanji -> hiragana. Add here (with a reason) when verification
#    shows JMdict/KANJIDIC2 picks the wrong default for lyrics/everyday use.
# ---------------------------------------------------------------------------
MANUAL_CORRECTIONS = {
    # 私: KANJIDIC2 lists わたくし first, JMdict's top entry is also formal;
    # in songs and everyday speech the default is わたし.
    "私": "わたし",
    # 愛: kun list ends in まな ("beloved"); the standalone word is あい.
    "愛": "あい",
    # 夜: standalone word is よる (や is the on-reading, only in compounds).
    "夜": "よる",
    # 見: standalone verb is みる (けん is the on-reading, only in compounds).
    "見": "みる",
    # 言: standalone verb is いう (こと is the noun "word"; げん/ごん are on).
    "言": "いう",
    # 生: JMdict's top single-kanji entry is なま ("raw"); as a standalone
    # noun in lyrics/everyday use the default is せい ("life").
    # (生きる/生まれる/生える are word-level — handled by overrides, not here.)
    "生": "せい",
    # 大: JMdict's top entry is だい; the standalone/prefix reading in
    # everyday words (大きい, 大きな) is おお.
    "大": "おお",
    # 小: standalone/prefix reading is こ (小さい, 小さな); しょう is on-only.
    "小": "こ",
    # 古: standalone adjective is ふるい (こ is the on-reading, compounds only).
    "古": "ふるい",
    # 方: JMdict's top entry is ほう ("direction"); in lyrics the person
    # reading かた (貴方→あなた is word-level) is the safer default.
    "方": "かた",
    # 何: standalone is なに (なん is the pre-noun form, e.g. 何人).
    "何": "なに",
    # 今: standalone is いま (こん is the on-reading, compounds only).
    "今": "いま",
    # 人: standalone is ひと (にん/じん are on-readings, compounds only).
    "人": "ひと",
    # 日: standalone is ひ (にち/じつ are on-readings, compounds only).
    "日": "ひ",
    # 月: standalone is つき (げつ/がつ are on-readings, compounds only).
    "月": "つき",
    # 年: standalone is とし (ねん is the on-reading, compounds only).
    "年": "とし",
    # 山: standalone is やま (さん is the on-reading, compounds only).
    "山": "やま",
    # 川: standalone is かわ (せん is the on-reading, compounds only).
    "川": "かわ",
    # 空: standalone is そら (くう is the on-reading, compounds only).
    "空": "そら",
    # 海: standalone is うみ (かい is the on-reading, compounds only).
    "海": "うみ",
    # 雨: standalone is あめ (う is the on-reading, compounds only).
    "雨": "あめ",
    # 雪: standalone is ゆき (せつ is the on-reading, compounds only).
    "雪": "ゆき",
    # 花: standalone is はな (か is the on-reading, compounds only).
    "花": "はな",
    # 木: standalone is き (もく is the on-reading, compounds only).
    "木": "き",
    # 金: standalone is かね (きん is the on-reading, compounds only).
    "金": "かね",
    # 土: standalone is つち (ど/と are on-readings, compounds only).
    "土": "つち",
    # 水: standalone is みず (すい is the on-reading, compounds only).
    "水": "みず",
    # 火: standalone is ひ (か is the on-reading, compounds only).
    "火": "ひ",
    # 心: standalone is こころ (しん is the on-reading, compounds only).
    "心": "こころ",
    # 手: standalone is て (しゅ is the on-reading, compounds only).
    "手": "て",
    # 目: standalone is め (もく is the on-reading, compounds only).
    "目": "め",
    # 口: standalone is くち (こう is the on-reading, compounds only).
    "口": "くち",
    # 耳: standalone is みみ (じ is the on-reading, compounds only).
    "耳": "みみ",
    # 足: standalone is あし (そく is the on-reading, compounds only).
    "足": "あし",
    # 車: standalone is くるま (しゃ is the on-reading, compounds only).
    "車": "くるま",
    # 星: standalone is ほし (せい is the on-reading, compounds only).
    "星": "ほし",
    # 田: standalone is た (でん is the on-reading, compounds only).
    "田": "た",
    # 電: standalone noun is でんき, but per-kanji the on-reading でん is
    # the only sensible default (no kun reading exists).
    "電": "でん",
    # 本: standalone is ほん (もと is the kun "origin", rare standalone).
    "本": "ほん",
    # 学: no kun reading; on-reading がく is the default.
    "学": "がく",
    # 新: standalone is あたらしい (しん is the on-reading, compounds only).
    "新": "あたらしい",
    # --- added from JMdict verification (single-kanji priority entries whose
    # top hit is a special/adverbial reading, not the standalone default) ---
    # 彼: JMdict top hit is あれ ("that", pronoun); modern default is かれ.
    "彼": "かれ",
    # 先: JMdict top hit is さっき ("a while ago"); default is さき.
    "先": "さき",
    # 抑: JMdict top hit is そもそも ("in the first place"); default おさえる.
    "抑": "おさえる",
    # 頓: JMdict top hit is ひたすら ("intently"); default is にわかに.
    "頓": "にわかに",
    # 前: JMdict top hit is さき ("tip"); standalone default is まえ.
    "前": "まえ",
    # 冷: JMdict top hit is ひや ("cold sake"); default is つめたい.
    "冷": "つめたい",
    # 許: JMdict top hit is もと ("under (guidance)"); default is ゆるす.
    "許": "ゆるす",
    # 比: JMdict top hit is ころ ("around (time)"); on-reading ひ is the
    # compound default (比較, 比率); the verb くらべる needs okurigana.
    "比": "ひ",
    # 因: JMdict top hit is もと ("origin"); on-reading いん is the
    # compound default (原因, 因果).
    "因": "いん",
}

HIRAGANA_RE = re.compile(r"^[ぁ-んー]+$")


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x20000 <= o <= 0x2A6DF
    )


def kata_to_hira(text: str) -> str:
    out = []
    for c in text:
        o = ord(c)
        if 0x30A1 <= o <= 0x30F6:  # katakana -> hiragana
            out.append(chr(o - 0x60))
        else:
            out.append(c)
    return "".join(out)


def clean_kun(reading: str) -> str:
    """Strip KANJIDIC2 okurigana '.' and bound '-' markers: み.る -> みる."""
    return reading.replace(".", "").replace("-", "").replace(" ", "").strip()


# ---------------------------------------------------------------------------
# Kana -> romaji (Hepburn, long vowels doubled) — copied from
# ~/workspace/srmusic-kanji-index/build_index.py (same convention).
# ---------------------------------------------------------------------------
_HIRA_SINGLE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "wi", "ゑ": "we", "を": "wo",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ん": "n",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo",
    "ゔ": "vu",
}
_DIGRAPH_BASE = {
    "き": "ky", "ぎ": "gy", "し": "sh", "じ": "j", "ち": "ch", "ぢ": "j",
    "に": "ny", "ひ": "hy", "び": "by", "ぴ": "py", "み": "my", "り": "ry",
}
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SOKUON = {"っ"}
_CHOUON = {"ー"}
_SKIP = {".", "-", "・", " ", "　"}


def kana_to_romaji(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in _SKIP:
            i += 1
            continue
        if c in _SOKUON:
            nxt = kana_to_romaji(text[i + 1:]) if i + 1 < n else ""
            if nxt and nxt[0] not in "aeiou":
                out.append(nxt[0])
            i += 1
            continue
        if c in _CHOUON:
            if out:
                last = out[-1]
                for ch in reversed(last):
                    if ch in "aeiou":
                        out.append(ch)
                        break
            i += 1
            continue
        if c in _DIGRAPH_BASE and i + 1 < n and text[i + 1] in _SMALL_Y:
            out.append(_DIGRAPH_BASE[c] + _SMALL_Y[text[i + 1]])
            i += 2
            continue
        roma = _HIRA_SINGLE.get(c)
        out.append(roma if roma is not None else "?")
        i += 1
    return "".join(out)


ROMAJI_TESTS = [
    ("ゆうべ", "yuube"), ("ふたり", "futari"), ("にどめ", "nidome"),
    ("おとな", "otona"), ("あなた", "anata"), ("きょう", "kyou"),
    ("とうきょう", "toukyou"), ("がっこう", "gakkou"), ("しゃ", "sha"),
    ("きゃ", "kya"), ("ほんとう", "hontou"), ("だいじょうぶ", "daijoubu"),
    ("がくせい", "gakusei"), ("しんぶん", "shinbun"),
    ("ちょっと", "chotto"), ("さびしさ", "sabishisa"),
    ("わたし", "watashi"), ("あい", "ai"), ("せい", "sei"),
]


# ---------------------------------------------------------------------------
# JMdict priority scoring
# ---------------------------------------------------------------------------
def pri_score(tags):
    best = 0
    for t in tags:
        if t == "ichi1":
            best = max(best, 100)
        elif t == "news1":
            best = max(best, 90)
        elif t.startswith("nf") and t[2:].isdigit():
            best = max(best, 85 - int(t[2:]))
        elif t == "spec1":
            best = max(best, 60)
        elif t == "spec2":
            best = max(best, 50)
        elif t == "gai1":
            best = max(best, 40)
    return best


def parse_jmdict_single_kanji():
    """Return {kanji: reading} from JMdict single-kanji priority entries."""
    best = {}  # kanji -> (score, reading)
    with gzip.open(JMDICT_GZ, "rb") as f:
        tree = ET.parse(f)
    root = tree.getroot()
    entries = 0
    skipped_adv = 0
    for entry in root.findall("entry"):
        entries += 1
        # Skip adverb-only entries: an adverb reading (e.g. 数→しばしば
        # "often", 熟→つくづく "deeply") is never the right per-kanji
        # default, even when it is the top priority entry for that kanji.
        senses = entry.findall("sense")
        if senses:
            all_pos = [p.text for s in senses for p in s.findall("pos")]
            if all_pos and all(
                p in ("adverb (fukushi)", "adverb taking the 'to' particle")
                for p in all_pos
            ):
                skipped_adv += 1
                continue
        kebs = []
        for ke in entry.findall("k_ele"):
            keb_el = ke.find("keb")
            if keb_el is None or not keb_el.text:
                continue
            keb = keb_el.text.strip()
            if len(keb) == 1 and is_cjk(keb):
                pris = [p.text for p in ke.findall("ke_pri") if p.text]
                kebs.append((keb, pris))
        if not kebs:
            continue
        # first r_ele reading (skip readings marked "no kanji")
        reading = None
        re_pris = []
        for re in entry.findall("r_ele"):
            if re.find("re_nokanji") is not None:
                continue
            reb_el = re.find("reb")
            if reb_el is None or not reb_el.text:
                continue
            reading = reb_el.text.strip()
            re_pris = [p.text for p in re.findall("re_pri") if p.text]
            break
        if not reading or not HIRAGANA_RE.match(reading):
            continue
        for keb, ke_pris in kebs:
            score = pri_score(ke_pris + re_pris)
            if score <= 0:
                continue  # only priority entries count for step 2
            cur = best.get(keb)
            if cur is None or score > cur[0]:
                best[keb] = (score, reading)
    print(f"JMdict entries scanned: {entries} (adverb-only entries skipped: {skipped_adv})")
    return {k: v[1] for k, v in best.items()}


# ---------------------------------------------------------------------------
# KANJIDIC2 parsing (all kanji, any grade)
# ---------------------------------------------------------------------------
def parse_kanjidic():
    kanji = {}  # literal -> (on_list, kun_list)
    with gzip.open(KANJIDIC_GZ, "rb") as f:
        tree = ET.parse(f)
    root = tree.getroot()
    for ch in root.findall("character"):
        literal_el = ch.find("literal")
        if literal_el is None or not literal_el.text:
            continue
        literal = literal_el.text.strip()
        on, kun = [], []
        for rm in ch.findall("reading_meaning/rmgroup"):
            for r in rm.findall("reading"):
                if r.text is None:
                    continue
                val = r.text.strip()
                if r.get("r_type") == "ja_on":
                    on.append(val)
                elif r.get("r_type") == "ja_kun":
                    kun.append(val)
        kanji[literal] = (on, kun)
    return kanji


def kanjidic_fallback(on, kun):
    """First kun (cleaned), else first on converted to hiragana."""
    for k in kun:
        c = clean_kun(k)
        if c and HIRAGANA_RE.match(c):
            return c
    for o in on:
        c = kata_to_hira(o)
        if c and HIRAGANA_RE.match(c):
            return c
    return None


# ---------------------------------------------------------------------------
def write_flat_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        items = sorted(data.items())  # sorted by Unicode codepoint
        for i, (k, v) in enumerate(items):
            f.write(json.dumps(k, ensure_ascii=False) + ": "
                    + json.dumps(v, ensure_ascii=False))
            f.write(",\n" if i < len(items) - 1 else "\n")
        f.write("}\n")


def main():
    for kana, want in ROMAJI_TESTS:
        got = kana_to_romaji(kana)
        assert got == want, f"romaji {kana}: got {got}, want {want}"
    print(f"romaji converter: {len(ROMAJI_TESTS)} tests OK")

    for k, v in MANUAL_CORRECTIONS.items():
        assert HIRAGANA_RE.match(v), f"manual correction not hiragana: {k} -> {v}"

    kanjidic = parse_kanjidic()
    print(f"KANJIDIC2 kanji: {len(kanjidic)}")
    jmdict = parse_jmdict_single_kanji()
    print(f"JMdict single-kanji priority readings: {len(jmdict)}")

    readings = {}
    sources = {"manual": 0, "jmdict": 0, "kanjidic": 0}
    skipped = []
    for literal in sorted(kanjidic):
        if literal in MANUAL_CORRECTIONS:
            readings[literal] = MANUAL_CORRECTIONS[literal]
            sources["manual"] += 1
        elif literal in jmdict:
            readings[literal] = jmdict[literal]
            sources["jmdict"] += 1
        else:
            on, kun = kanjidic[literal]
            fb = kanjidic_fallback(on, kun)
            if fb is None:
                skipped.append(literal)
                continue
            readings[literal] = fb
            sources["kanjidic"] += 1

    romaji = {k: kana_to_romaji(v) for k, v in readings.items()}
    for k, v in romaji.items():
        assert "?" not in v, f"romaji unmapped for {k}: {readings[k]} -> {v}"

    write_flat_json(READINGS_JSON, readings)
    write_flat_json(READINGS_ROMAJI_JSON, romaji)

    print(f"sources: {sources} (total {len(readings)})")
    print(f"skipped (no usable reading): {len(skipped)} {skipped}")
    print(f"readings.json: {READINGS_JSON.stat().st_size/1024:.1f} KB")
    print(f"readings_romaji.json: {READINGS_ROMAJI_JSON.stat().st_size/1024:.1f} KB")
    print("BUILD OK")


if __name__ == "__main__":
    main()
