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
  4. Unihan (skipped kanji ONLY): kJapaneseKun/kJapaneseOn from
     Unihan_Readings.txt, plus the 18 metric-unit words (katakana, from the
     legacy kJapanese field — the only place it is trusted) and 2 JMdict
     fish readings (𩸽→ほっけ, 𩺊→あら). See parse_unihan().
  5. Skip kanji with no usable reading rather than inventing one.

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
UNIHAN_READINGS_TXT = HERE / "Unihan_Readings.txt"  # local only, NOT pushed
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
    # 学: kun reading まなぶ exists (学ぶ "to learn"), but がく is kept:
    # 学's standalone use is in compounds (学校, 学生, 大学) where the
    # word-level reading wins anyway; まなぶ is a word, handled word-level.
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

# ---------------------------------------------------------------------------
# 2. VERB CORRECTIONS (2026-11-20 audit) — merged into MANUAL_CORRECTIONS
#    at build time, same priority.
#
#    Audit method: every kanji whose current default was an on-reading
#    (compound-only, never stands alone) or a rare/archaic noun, while its
#    KANJIDIC2 kun list holds an everyday verb, was checked against the
#    verb dictionary form (first kun verb, '.' okurigana marker stripped).
#    The verb form wins when it is the kanji's everyday standalone word:
#    行く/来る/食べる/死ぬ/飛ぶ/歩く/座る/着る/選ぶ/通る/運ぶ/決める/
#    流れる/変わる is word-level — see VERB_KEEP for the deliberate keeps.
#
#    Why the dictionary form (not the stem): the stem is a prefix of the
#    verb's conjugations, so the aligner can match it against okurigana
#    (e.g. 行く→いく matches 行った/行って via stem い). An on-reading
#    (ぎょう/らい/しょく/ほ/ざ) matches nothing when the kanji is a verb.
#
#    Two special picks (not the first kun verb in KANJIDIC2 order):
#      食→たべる (first kun is くう — rough/masculine; たべる is neutral)
#      歩→あるく (あゆむ is literary; あるく is the standard verb)
#    Four adjective picks (same "on-reading is useless" logic, i-adjective
#    is the everyday word): 多→おおい, 弱→よわい, 悪→わるい, 軽→かるい.
#    熟→うれる was already the verb default (熟れる "to ripen") — kept.
# ---------------------------------------------------------------------------
VERB_CORRECTIONS = {
    # on-reading -> everyday verb (53 entries: 49 verbs + 4 adjectives)
    "行": "いく",   # ぎょう only in compounds (銀行, 行列); 行く is the verb
    "来": "くる",   # らい only in compounds (未来, 来年); 来る is the verb
    "食": "たべる",  # しょく only in compounds; たべる is the neutral verb
    "書": "かく",   # ふみ is archaic ("letter"); 書く is the everyday verb
    "会": "あう",   # かい ("meeting") is formal; in lyrics 会う/会いたい dominates
    "座": "すわる",  # ざ only in compounds (座席, 講座); 座る is the verb
    "歩": "あるく",  # ほ only in compounds (歩行, 散歩); 歩く is the verb
    "住": "すむ",   # じゅう only in compounds (住所); 住む is the verb
    "作": "つくる",  # さく as a standalone noun is rare/literary; 作る is the verb
    "合": "あう",   # ごう only in compounds (合唱, 合格); 合う is the verb
    "回": "まわる",  # かい is a counter/compounds; 回る is the verb
    "得": "える",   # とく only in compounds (獲得); 得る is the verb
    "急": "いそぐ",  # きゅう only in compounds; 急ぐ is the verb
    "想": "おもう",  # そう only in compounds; 想う is the lyric verb ("to long for")
    "拾": "ひろう",  # じゅう only in compounds; 拾う is the verb
    "断": "たつ",   # だん only in compounds (判断); 断つ is the verb
    "有": "ある",   # ゆう only in compounds; 有る is the verb ("to exist")
    "架": "かける",  # か only in compounds; 架ける is the verb
    "決": "きめる",  # けつ only in compounds; 決める is the verb
    "泊": "とまる",  # はく only in compounds; 泊まる is the verb
    "流": "ながれる",  # りゅう only in compounds; 流れる is the verb
    "渇": "かわく",  # かつ only in compounds; 渇く is the verb
    "用": "もちいる",  # よう only in compounds; 用いる is the verb
    "畳": "たたむ",  # じょう only in compounds; 畳む is the verb
    "疑": "うたがう",  # ぎ only in compounds; 疑う is the verb
    "発": "たつ",   # はつ only in compounds; 発つ is the verb ("to depart")
    "直": "なおす",  # じき only in compounds; 直す is the verb
    "着": "きる",   # ちゃく only in compounds; 着る is the verb ("to wear")
    "積": "つむ",   # せき only in compounds; 積む is the verb
    "編": "あむ",   # へん only in compounds; 編む is the verb ("to knit")
    "腫": "はれる",  # しゅ only in compounds; 腫れる is the verb
    "観": "みる",   # かん only in compounds; 観る is the verb ("to watch")
    "負": "まける",  # ふ only in compounds; 負ける is the verb
    "貫": "つらぬく",  # かん only in compounds; 貫く is the verb
    "費": "ついやす",  # ひ only in compounds; 費やす is the verb
    "超": "こえる",  # ちょう only in compounds; 超える is the verb
    "通": "とおる",  # つう only in compounds; 通る is the verb
    "運": "はこぶ",  # うん only in compounds; 運ぶ is the verb
    "選": "えらぶ",  # せん only in compounds; 選ぶ is the verb
    "集": "あつまる",  # しゅう only in compounds; 集まる is the verb
    "飛": "とぶ",   # ひ only in compounds; 飛ぶ is the verb
    "仕": "つかえる",  # し only in compounds; 仕える is the verb ("to serve")
    "任": "まかせる",  # にん only in compounds; 任せる is the verb
    "兼": "かねる",  # けん only in compounds; 兼ねる is the verb
    "加": "くわえる",  # か only in compounds; 加える is the verb
    "動": "うごく",  # どう only in compounds; 動く is the verb
    "利": "きく",   # り as a standalone word is rare; 利く is the verb
    "倒": "たおれる",  # さかしま is archaic ("upside-down"); 倒れる is the verb
    "溺": "おぼれる",  # いばり is archaic; 溺れる is the everyday verb
    # adjectives (same logic: on-reading never stands alone)
    "多": "おおい",  # た only in compounds; 多い is the adjective
    "弱": "よわい",  # じゃく only in compounds; 弱い is the adjective
    "悪": "わるい",  # あく only in compounds; 悪い is the adjective
    "軽": "かるい",  # けい only in compounds; 軽い is the adjective
}

# ---------------------------------------------------------------------------
# 3. VERB KEEPS — kanji with a verb option that was deliberately NOT taken.
#    Each entry: kanji -> (expected current reading, reason). Verified by an
#    assert in main() so a future rebuild cannot silently change them.
#    Rule: the noun/adjective/stem form wins when it is itself a common
#    standalone word in lyrics (笑い, 終わり, 揺り, 話, 歌, 曲), when it is
#    the verb stem itself (出→で, 寝→ね, 萎→な — the stem is a prefix of
#    出る/出た/寝る/寝た/なえる, so it already aligns), or when the verb is
#    archaic/formal/rare while the noun is the everyday word.
#    Numbers are here because the audit regex wrongly flagged counters
#    (ひとつ/みつ/よつ end in つ) — they are nouns, not verbs.
# ---------------------------------------------------------------------------
VERB_KEEP = {
    "生": ("せい", "noun 'life' wins; 生きる/生まれる are word-level"),
    "歌": ("うた", "noun 'song' wins; 歌う is word-level"),
    "笑": ("わらい", "noun 'laughter' wins; 笑う is word-level"),
    "話": ("はなし", "noun 'story' wins; 話す is word-level"),
    "終": ("おわり", "noun 'the end' wins; 終わる is word-level"),
    "揺": ("ゆり", "noun 'swinging' wins; 揺れる is word-level"),
    "出": ("で", "verb stem itself; prefix of 出る/出た/出て"),
    "寝": ("ね", "verb stem itself; prefix of 寝る/寝た/寝て"),
    "萎": ("な", "verb stem itself; prefix of なえる/なえた"),
    "変": ("へん", "standalone word 変な/変だ is lyric-common; 変わる word-level"),
    "曲": ("きょく", "noun 'song/track' — music context; 曲がる word-level"),
    "関": ("せき", "noun 'barrier' wins; 関わる is word-level"),
    "一": ("いち", "number; ひとつ is a counter, not a verb"),
    "二": ("に", "number; ふたつ is a counter, not a verb"),
    "三": ("さん", "number; みつ is a counter, not a verb"),
    "四": ("し", "number; よつ is a counter, not a verb"),
    "五": ("ご", "number; いつつ is a counter, not a verb"),
    "六": ("ろく", "number; むつ is a counter, not a verb"),
    "七": ("しち", "number; ななつ is a counter, not a verb"),
    "八": ("はち", "number; やつ is a counter, not a verb"),
    "九": ("きゅう", "number; ここのつ is a counter, not a verb"),
    "分": ("ぶん", "noun 'part/minute' wins; 分ける is word-level"),
    "別": ("べつ", "noun/adverb wins; 分かれる/分ける are word-level"),
    "例": ("れい", "noun 'example' wins; たとえる is word-level"),
    "和": ("わ", "noun 'harmony/Japan' wins; 和む/和らぐ are word-level"),
    "対": ("たい", "noun wins; こたえる/むかう are word-level"),
    "正": ("せい", "noun wins; ただす is word-level"),
    "点": ("てん", "noun wins; つける/つく are word-level"),
    "量": ("りょう", "noun wins; はかる is word-level"),
    "語": ("ご", "noun wins; かたる is word-level"),
    "記": ("き", "noun wins; しるす is literary"),
    "試": ("ためし", "noun wins; ためす is word-level"),
    "祝": ("いわい", "noun wins; いわう is word-level"),
    "祭": ("まつり", "noun wins; まつる is word-level"),
    "答": ("こたえ", "noun wins; こたえる is word-level"),
    "数": ("かず", "noun wins; かぞえる is word-level"),
    "宿": ("やど", "noun wins; やどる is word-level"),
    "傷": ("きず", "noun wins; いたむ is word-level"),
    "光": ("ひかり", "noun wins; ひかる is word-level"),
    "夢": ("ゆめ", "noun wins; ゆめみる is a compound verb"),
    "恋": ("こい", "noun wins; こう is a rare verb"),
    "履": ("くつ", "noun 'shoes' wins; 履く is word-level"),
    "侍": ("さむらい", "noun wins; はべる is archaic"),
    "候": ("そうろう", "archaic auxiliary; no better default exists"),
    "参": ("さん", "まいる is formal/humble, rare in lyrics; さん is default"),
    "医": ("い", "いやす is archaic; compounds dominate"),
    "図": ("ず", "はかる is uncommon; ず is the standalone noun"),
    "報": ("ほう", "むくいる is archaic; ほう is the noun"),
    "商": ("しょう", "あきなう is archaic; しょう is the noun"),
    "楽": ("らく", "noun wins; たのしむ/このむ are word-level"),
    "権": ("けん", "はかる is a rare verb for 権; けん is the noun"),
    "欲": ("よく", "ほっする is rare; よく is the noun"),
    "興": ("きょう", "noun 'interest' wins; 興る is word-level"),
    "著": ("ちょ", "あらわす is literary; ちょ is the compound default"),
    "説": ("せつ", "とく is literary; せつ is the compound default"),
    "計": ("けい", "noun wins; はかる is word-level"),
    "訓": ("くん", "おしえる is rare for 訓; くん is the noun"),
    "象": ("ぞう", "noun wins; かたどる is rare"),
    "賛": ("さん", "賛する is a suru-verb; さん is the noun default"),
    "賞": ("しょう", "noun wins; ほめる is word-level"),
    "質": ("しつ", "noun wins; ただす is word-level"),
    "益": ("えき", "noun wins; ます is word-level"),
    "精": ("せい", "しらげる is rare; せい is the noun"),
    "約": ("やく", "つづまる is rare; やく is the noun"),
    "群": ("ぐん", "noun wins; むれる is word-level"),
    "能": ("のう", "あたう is rare; のう is the noun"),
    "禅": ("ぜん", "ゆずる is rare; ぜん is the noun"),
    "漁": ("りょう", "noun wins; あさる is word-level"),
    "猟": ("りょう", "noun wins; かる is word-level"),
    "率": ("りつ", "ひきいる is rare; りつ is the noun"),
    "準": ("じゅん", "じゅんじる is formal; じゅん is the noun"),
    "損": ("そん", "noun 'loss' wins; 損なう is word-level"),
    "敵": ("てき", "noun wins; かなう is rare"),
    "兆": ("ちょう", "きざす is rare; ちょう is the noun"),
    "免": ("めん", "まぬかれる is rare; めん is the noun"),
    "勇": ("ゆう", "いさむ is literary; ゆう is the noun 'courage'"),
    "反": ("はん", "noun wins; そる/かえる are word-level"),
    "号": ("ごう", "noun wins; さけぶ is rare"),
    "怪": ("かい", "noun wins; あやしむ is word-level"),
    "挺": ("ちょう", "counter/noun; ぬく is rare"),
    "柵": ("さく", "noun wins; しがらむ is rare"),
    "極": ("ごく", "adverb 'extremely' wins; 極める is word-level"),
    "汎": ("はん", "rare kanji; noun wins; ただよう is word-level"),
    "没": ("ぼつ", "おぼれる is usually written 溺れる; ぼつ is default"),
    "沢": ("さわ", "noun wins; うるおす is word-level"),
    "泥": ("どろ", "noun wins; なずむ is rare"),
    "沖": ("おき", "noun wins; ちゅうする is rare"),
    "熟": ("うれる", "verb already (熟れる 'to ripen'); じゅく is on-only"),
    "算": ("そろ", "そろ is the JMdict default; no everyday verb exists"),
    "尚": ("なお", "なお is the standalone word; no verb reading exists"),
}

# ---------------------------------------------------------------------------
# 4. JMDICT EXTRA (2026-11-20) — single-kanji readings that Unihan cannot
#    supply. These two kanji have no kJapaneseKun/kJapaneseOn at all; their
#    readings come from JMdict single-kanji entries (checked live).
#    The other 7 JMdict unit entries (瓩/竏/竓/籵/粍/粨/糎) agree with the
#    Unihan step below and are covered by it — see JMDICT_UNIT_EXPECTED.
# ---------------------------------------------------------------------------
JMDICT_EXTRA = {
    "𩸽": "ほっけ",  # fish (hokke); JMdict single-kanji entry, no Unihan reading
    "𩺊": "あら",    # fish (ara); JMdict single-kanji entry, no Unihan reading
}

# Cross-check: Unihan-derived readings for these 7 must equal the JMdict
# single-kanji values (verified 2026-11-20; asserted in main()).
JMDICT_UNIT_EXPECTED = {
    "瓩": "キログラム", "竏": "キロリットル", "竓": "ミリリットル",
    "籵": "デカメートル", "粍": "ミリメートル", "粨": "ヘクトメートル",
    "糎": "センチメートル",
}

# Katakana values are allowed ONLY for these 18 metric-unit words.
# Every other readings.json value must be hiragana. Romaji for these is
# Hepburn of the katakana (キログラム -> kiroguramu, センチメートル ->
# senchimeetoru). The app treats them like any other reading.
KATAKANA_ALLOWED = {
    "瓧": "デカグラム", "瓩": "キログラム", "瓰": "デシグラム",
    "瓱": "ミリグラム", "瓲": "トン", "瓸": "ヘクトグラム",
    "甅": "センチグラム",
    "竍": "デカリットル", "竏": "キロリットル", "竓": "ミリリットル",
    "竕": "デシリットル", "竡": "ヘクトリットル", "竰": "センチリットル",
    "籵": "デカメートル", "粁": "キロメートル", "粍": "ミリメートル",
    "粨": "ヘクトメートル", "糎": "センチメートル",
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
    text = kata_to_hira(text)  # katakana unit words (キログラム) convert too
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
# Romaji (Unihan kJapaneseKun/kJapaneseOn, uppercase Hepburn) -> kana.
# Long vowels: hiragana mode doubles the vowel (OO -> おお), katakana mode
# uses the chouonpu (OO -> オー) — standard katakana spelling.
# ---------------------------------------------------------------------------
_ROMAJI_SYLL = {
    # digraphs first (longest match wins)
    "KYA": "きゃ", "KYU": "きゅ", "KYO": "きょ",
    "GYA": "ぎゃ", "GYU": "ぎゅ", "GYO": "ぎょ",
    "SHA": "しゃ", "SHU": "しゅ", "SHE": "しぇ", "SHO": "しょ",
    "JA": "じゃ", "JU": "じゅ", "JE": "じぇ", "JO": "じょ",
    "CHA": "ちゃ", "CHU": "ちゅ", "CHE": "ちぇ", "CHO": "ちょ",
    "NYA": "にゃ", "NYU": "にゅ", "NYO": "にょ",
    "HYA": "ひゃ", "HYU": "ひゅ", "HYO": "ひょ",
    "BYA": "びゃ", "BYU": "びゅ", "BYO": "びょ",
    "PYA": "ぴゃ", "PYU": "ぴゅ", "PYO": "ぴょ",
    "MYA": "みゃ", "MYU": "みゅ", "MYO": "みょ",
    "RYA": "りゃ", "RYU": "りゅ", "RYO": "りょ",
    "KA": "か", "KI": "き", "KU": "く", "KE": "け", "KO": "こ",
    "SA": "さ", "SHI": "し", "SU": "す", "SE": "せ", "SO": "そ",
    "TA": "た", "CHI": "ち", "TSU": "つ", "TE": "て", "TO": "と",
    "NA": "な", "NI": "に", "NU": "ぬ", "NE": "ね", "NO": "の",
    "HA": "は", "HI": "ひ", "FU": "ふ", "HE": "へ", "HO": "ほ",
    "MA": "ま", "MI": "み", "MU": "む", "ME": "め", "MO": "も",
    "YA": "や", "YU": "ゆ", "YO": "よ",
    "RA": "ら", "RI": "り", "RU": "る", "RE": "れ", "RO": "ろ",
    "WA": "わ", "WO": "を",
    "GA": "が", "GI": "ぎ", "GU": "ぐ", "GE": "げ", "GO": "ご",
    "ZA": "ざ", "JI": "じ", "ZU": "ず", "ZE": "ぜ", "ZO": "ぞ",
    "DA": "だ", "DE": "で", "DO": "ど",
    "BA": "ば", "BI": "び", "BU": "ぶ", "BE": "べ", "BO": "ぼ",
    "PA": "ぱ", "PI": "ぴ", "PU": "ぷ", "PE": "ぺ", "PO": "ぽ",
    "A": "あ", "I": "い", "U": "う", "E": "え", "O": "お",
    "N": "ん", "VU": "ゔ",
}
_VOWEL_KANA = {"A": "あ", "I": "い", "U": "う", "E": "え", "O": "お"}


def romaji_to_kana(token: str, katakana: bool = False) -> str:
    """Convert one uppercase Hepburn romaji token to kana.

    Always parses to hiragana first (which handles digraphs, sokuon,
    syllabic N and doubled vowels correctly), then, for katakana output,
    collapses a vowel that repeats the previous kana's vowel into a
    chouonpu (めえとる -> メートル). That collapse is only correct when
    the doubled vowel IS a long vowel — true for the metric-unit words
    this converter is used for (メートル, リットル, グラム); kun readings
    like ほのお are always converted in hiragana mode, never katakana.
    Returns "" on an unparseable token.
    """
    s = token.strip().upper()
    if not s or not re.match(r"^[A-Z]+$", s):
        return ""
    out = []
    i = 0
    n = len(s)
    while i < n:
        # long vowel: AA/II/UU/EE/OO (same vowel twice) -> doubled kana
        if i + 1 < n and s[i] == s[i + 1] and s[i] in "AIUEO":
            out.append(_VOWEL_KANA[s[i]] + _VOWEL_KANA[s[i]])
            i += 2
            continue
        # sokuon: doubled consonant (not N)
        if i + 1 < n and s[i] == s[i + 1] and s[i] not in "AEIOUN":
            out.append("っ")
            i += 1
            continue
        # syllabic N: N not followed by a vowel or Y
        if s[i] == "N" and (i + 1 >= n or s[i + 1] not in "AIUEOY"):
            out.append("ん")
            i += 1
            continue
        # longest-match syllable (3 then 2 then 1 chars)
        matched = None
        for L in (3, 2, 1):
            cand = s[i:i + L]
            if cand in _ROMAJI_SYLL:
                matched = _ROMAJI_SYLL[cand]
                i += L
                break
        if matched is None:
            return ""
        out.append(matched)
    kana = "".join(out)
    if not katakana:
        return kana
    return _hira_to_kata_long(kana)


_KANA_VOWEL = {}
for _row, _v in (("あかさたなはまやらわがざだばぱ", "a"),
                 ("いきしちにひみりぎじぢびぴ", "i"),
                 ("うくすつぬふむゆるぐずづぶぷ", "u"),
                 ("えけせてねへめれげぜでべぺ", "e"),
                 ("おこそとのほもよろをごぞどぼぽ", "o")):
    for _c in _row:
        _KANA_VOWEL[_c] = _v
_KANA_VOWEL.update({"ゃ": "a", "ゅ": "u", "ょ": "o", "ぁ": "a", "ぃ": "i",
                    "ぅ": "u", "ぇ": "e", "ぉ": "o"})
_VOWEL_KANA_SET = {"あ": "a", "い": "i", "う": "u", "え": "e", "お": "o"}


def _hira_to_kata_long(kana: str) -> str:
    """Hiragana -> katakana, collapsing a repeated vowel into ー."""
    out = []
    prev_vowel = None
    for c in kana:
        if c in _VOWEL_KANA_SET and prev_vowel == _VOWEL_KANA_SET[c]:
            out.append("ー")
        elif 0x3041 <= ord(c) <= 0x3096:
            out.append(chr(ord(c) + 0x60))
        else:
            out.append(c)
        prev_vowel = _KANA_VOWEL.get(c, prev_vowel if c == "っ" else None)
    return "".join(out)


ROMAJI_TO_KANA_TESTS = [
    ("SAKI", False, "さき"), ("TOBARI", False, "とばり"),
    ("HONOO", False, "ほのお"), ("SEKI", False, "せき"),
    ("TATAKAU", False, "たたかう"),
    ("KIROGURAMU", True, "キログラム"),
    ("DEKAMEETORU", True, "デカメートル"),
    ("SENCHIMEETORU", True, "センチメートル"),
    ("KIRORITTORU", True, "キロリットル"),
    ("DESHIRITTORU", True, "デシリットル"),
    ("HEKUTORITTORU", True, "ヘクトリットル"),
]


# ---------------------------------------------------------------------------
# Unihan readings (step 4, skipped kanji only).
# File: Unihan_Readings.txt (Unicode 18.0.0, local only, NOT pushed).
# Provenance: https://www.unicode.org/Public/UNIDATA/Unihan.zip
# ---------------------------------------------------------------------------
# Radical names are NOT pronunciations — never use them as readings.
_RADICAL_NAME_ENDINGS = ("HEN", "KANMURI", "GASHIRA", "KAMAE", "NYOU")
_UNIT_RE = re.compile(r"^(?:[ァ-ヶー]+(グラム|リットル|メートル)|トン)$")
# A Kun token is a metric unit when it ends in one of these romaji tails
# (or is exactly TON). The legacy-field unit rule below fires ONLY when
# the Kun token matches — otherwise トン would match hundreds of kanji
# whose on-reading is トン (㹠 "pig" etc.), which is NOT a unit word.
_UNIT_KUN_TAILS = ("GURAMU", "GURA", "RITTORU", "RITTO", "MEETORU", "MEETO")


def parse_unihan():
    """Return {kanji: kana} for kanji with a real Japanese reading in Unihan.

    Rules (all decided from the data, 2026-11-20):
    - Only kJapaneseKun / kJapaneseOn are used. The legacy kJapanese field
      is UNRELIABLE for arbitrary kanji (it lists 笑's readings for 关,
      and 開's readings for 开) — it is used ONLY for the 18 metric-unit
      words, where it holds the complete katakana word and the Kun romaji
      is truncated in Unihan itself (瓸 HEKUTOGURA, 甅 SENCHIGURA,
      竍 DEKARITTO, 竕 DESHIRITTO, 竡 HEKUTORITTO, 竰 SENCHIRITTO,
      糎 SENCHIMEETO — the full forms are デカ/ヘクト/センチ + unit).
    - First token only (TOBARI KAYA -> とばり; TATAKAU ARASOU -> たたかう).
    - Kun -> hiragana, On -> hiragana (same convention as KANJIDIC2).
    - Unit words -> katakana, taken from the legacy field (full form).
    - Radical names (TSUMEKANMURI, USHIHEN, KEMONOHEN, ...) excluded.
    """
    raw = {}  # kanji -> {field: value}
    with open(UNIHAN_READINGS_TXT, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            cp, field, val = parts[0], parts[1], parts[2]
            if field not in ("kJapaneseKun", "kJapaneseOn", "kJapanese"):
                continue
            try:
                ch = chr(int(cp[2:], 16))
            except ValueError:
                continue
            raw.setdefault(ch, {})[field] = val
    out = {}
    for ch, fields in raw.items():
        # 1) metric-unit words: legacy field holds the full katakana word,
        #    BUT only when the Kun reading is itself a unit word (see the
        #    _UNIT_KUN_TAILS gate above). Without the gate, トン alone
        #    would match ~300 kanji whose on-reading is トン (㹠 "pig"...).
        kun_val = fields.get("kJapaneseKun", "")
        kun_first = kun_val.split()[0] if kun_val else ""
        if kun_first == "TON" or kun_first.endswith(_UNIT_KUN_TAILS):
            legacy = fields.get("kJapanese", "")
            for tok in legacy.split():
                if _UNIT_RE.match(tok):
                    out[ch] = tok
                    break
        if ch in out:
            continue
        # 2) first Kun token, else first On token, romaji -> hiragana.
        for field in ("kJapaneseKun", "kJapaneseOn"):
            val = fields.get(field)
            if not val:
                continue
            token = val.split()[0]
            if token.endswith(_RADICAL_NAME_ENDINGS):
                continue  # radical name, not a pronunciation; try On
            kana = romaji_to_kana(token)
            if kana and HIRAGANA_RE.match(kana):
                out[ch] = kana
                break
    return out


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

    for token, kata, want in ROMAJI_TO_KANA_TESTS:
        got = romaji_to_kana(token, katakana=kata)
        assert got == want, f"romaji->kana {token}: got {got}, want {want}"
    print(f"romaji->kana converter: {len(ROMAJI_TO_KANA_TESTS)} tests OK")

    all_manual = {**MANUAL_CORRECTIONS, **VERB_CORRECTIONS}
    assert not (set(MANUAL_CORRECTIONS) & set(VERB_CORRECTIONS)), \
        "manual and verb corrections overlap"
    for k, v in all_manual.items():
        assert HIRAGANA_RE.match(v), f"manual correction not hiragana: {k} -> {v}"
    for k, v in JMDICT_EXTRA.items():
        assert HIRAGANA_RE.match(v), f"jmdict extra not hiragana: {k} -> {v}"

    kanjidic = parse_kanjidic()
    print(f"KANJIDIC2 kanji: {len(kanjidic)}")
    jmdict = parse_jmdict_single_kanji()
    print(f"JMdict single-kanji priority readings: {len(jmdict)}")
    unihan = parse_unihan()
    print(f"Unihan readings (Kun/On + unit words): {len(unihan)}")

    readings = {}
    sources = {"manual": 0, "jmdict": 0, "kanjidic": 0,
               "unihan": 0, "jmdict_extra": 0}
    skipped = []
    for literal in sorted(kanjidic):
        if literal in all_manual:
            readings[literal] = all_manual[literal]
            sources["manual"] += 1
        elif literal in jmdict:
            readings[literal] = jmdict[literal]
            sources["jmdict"] += 1
        else:
            on, kun = kanjidic[literal]
            fb = kanjidic_fallback(on, kun)
            if fb is not None:
                readings[literal] = fb
                sources["kanjidic"] += 1
            elif literal in unihan:
                readings[literal] = unihan[literal]
                sources["unihan"] += 1
            elif literal in JMDICT_EXTRA:
                readings[literal] = JMDICT_EXTRA[literal]
                sources["jmdict_extra"] += 1
            else:
                skipped.append(literal)

    # Verification: verb audit keeps + Unihan/JMdict cross-checks.
    for k, (want, why) in VERB_KEEP.items():
        got = readings.get(k)
        assert got == want, f"verb keep broken: {k} is {got}, want {want} ({why})"
    print(f"verb keeps verified: {len(VERB_KEEP)}")
    for k, want in JMDICT_UNIT_EXPECTED.items():
        got = readings.get(k)
        assert got == want, f"unit cross-check failed: {k} is {got}, want {want}"
    print(f"unit cross-checks verified: {len(JMDICT_UNIT_EXPECTED)}")
    for k, want in KATAKANA_ALLOWED.items():
        assert readings.get(k) == want, f"katakana unit wrong: {k}"
    # Values: hiragana, except the 18 documented katakana unit words.
    for k, v in readings.items():
        assert HIRAGANA_RE.match(v) or k in KATAKANA_ALLOWED, \
            f"bad value: {k} -> {v}"

    romaji = {k: kana_to_romaji(v) for k, v in readings.items()}
    for k, v in romaji.items():
        assert re.match(r"^[a-z]+$", v), f"romaji bad for {k}: {readings[k]} -> {v}"

    write_flat_json(READINGS_JSON, readings)
    write_flat_json(READINGS_ROMAJI_JSON, romaji)

    print(f"sources: {sources} (total {len(readings)})")
    print(f"skipped (no usable reading): {len(skipped)}")
    print(f"  sample: {''.join(skipped[:60])}")
    print(f"readings.json: {READINGS_JSON.stat().st_size/1024:.1f} KB")
    print(f"readings_romaji.json: {READINGS_ROMAJI_JSON.stat().st_size/1024:.1f} KB")
    print("BUILD OK")


if __name__ == "__main__":
    main()
