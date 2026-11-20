# SRMusic-JP

Flat kanji default-reading index for SRMusic — one kanji, one default
hiragana reading, so the app can pronounce Japanese lyrics fast.

## Why this repo exists

The old index
([SRMusic-JP-Index](https://github.com/Sanggi18/SRMusic-JP-Index)) stores
every reading of every kanji as arrays (`on[]`, `kun[]`). The app has to
scan those arrays and guess which reading fits — and the guess is often
wrong: `私` lists `わたくし` first, so lyrics came out as
"watakushi" instead of "watashi".

This repo stores **one default reading per kanji, in a flat object**:

```json
{"私": "わたし", "愛": "あい", "夜": "よる"}
```

The app fetches one small file and looks a kanji up in O(1). No array
scan, no guessing, and the default is the everyday/lyric reading, not
the formal dictionary-first one.

## Files

| File | Size | Contents |
| --- | --- | --- |
| `readings.json` | ~236 KB | **12,364 entries**: `{kanji: hiragana}`, keys sorted by Unicode codepoint |
| `readings_romaji.json` | ~197 KB | Same 12,364 keys, Hepburn romaji values (`{"私": "watashi"}`) |
| `build_readings.py` | — | Reproducible build script (re-run gives byte-identical files) |
| `JMdict_e.gz` | 10.6 MB | Word-level source, **kept locally only — not pushed** (see below) |
| `Unihan_Readings.txt` | 8.5 MB | Unihan readings source (Unicode 18.0.0), **kept locally only — not pushed** |

Romaji convention: Hepburn, long vowels doubled, no macrons
(`ゆうべ` → `yuube`, `きょう` → `kyou`, `がっこう` → `gakkou`).

## Fetch pattern (app)

```kotlin
// Fetch once, cache on device (e.g. 30 days, like the old index).
// readings.json:
https://raw.githubusercontent.com/Sanggi18/srmusic-jp/main/readings.json
// readings_romaji.json:
https://raw.githubusercontent.com/Sanggi18/srmusic-jp/main/readings_romaji.json
```

## Lookup order the app should use

1. **Word level first** — `lyric_overrides.json` from SRMusic-JP-Index
   (昨夜 → ゆうべ, 二人 → ふたり, 二度目 → にどめ, …). Whole words
   beat per-kanji readings every time.
2. **Per-kanji fallback** — this repo's `readings.json` (one lookup per
   kanji). Use `readings_romaji.json` if the app wants romaji directly
   instead of converting kana itself.
3. **Last resort** — Jisho API (`https://jisho.org/api/v1/search/words`)
   or Google Translate, then cache the result on device.

## How the default reading is chosen

In order (see `MANUAL_CORRECTIONS` at the top of `build_readings.py`):

1. **Manual lyric corrections (50) + verb corrections (53)** — win over
   everything. These exist because the automatic steps get them wrong,
   e.g. `私 → わたし` (not `わたくし`), `愛 → あい` (not `まな` /
   `いとしい`), `言 → いう` (not `げん`), `電 → でん` (not `いなずま`),
   `生 → せい` (not `なま` / `いきる`), `本 → ほん` (not `もと`).
   The 53 verb corrections (2026-11-20 audit, `VERB_CORRECTIONS` in the
   script) fix kanji whose default was a compound-only on-reading or a
   rare/archaic noun while the everyday word is a verb: `行 → いく`
   (not `ぎょう`), `来 → くる` (not `らい`), `食 → たべる` (not
   `しょく`), `書 → かく` (not `ふみ`), `会 → あう` (not `かい`),
   `座 → すわる`, `歩 → あるく`, `着 → きる`, `選 → えらぶ`,
   `通 → とおる`, `決 → きめる`, `流 → ながれる`, plus 4
   i-adjectives (`多 → おおい`, `弱 → よわい`, `悪 → わるい`,
   `軽 → かるい`). The audit deliberately KEPT the noun/stem form for
   90 kanji (`VERB_KEEP` in the script, asserted on every rebuild) —
   e.g. `歌 → うた`, `笑 → わらい`, `話 → はなし`, `終 → おわり`,
   `曲 → きょく` (a song, not "to bend"), `変 → へん` (`変な` is
   lyric-common; `変わる` is word-level), `出 → で` and `寝 → ね`
   (the verb stem itself, a prefix of `出る`/`出た`/`寝る`/`寝た`),
   and all nine numerals (`一 → いち`; `ひとつ` etc. are counters,
   not verbs).
2. **JMdict single-kanji words (1,676)** — entries whose kanji element is
   exactly that one kanji, preferring priority tags
   (`ichi1`, `news1`, `nf01`–`nf24`, `spec1/2`). Adverb-only entries are
   skipped (e.g. `数 → しばしば` "often" and `熟 → つくづく` "deeply"
   are adverbs, never a kanji's default reading).
3. **KANJIDIC2 fallback (10,559)** — first kun reading (okurigana `.`
   and bound `-` markers stripped, e.g. `み.る` → `みる`), else first
   on reading converted to hiragana.
4. **Unihan (24) + JMdict extras (2)** — kanji that steps 1–3 all skip.
   Unihan `kJapaneseKun`/`kJapaneseOn` (romaji → kana) fills 6
   (`㟢 → さき`, `㡡 → とばり`, `熖 → ほのお`, `碵 → せき`,
   `鬥 → たたかう`, `𥔎 → さき`), and the legacy `kJapanese` field —
   trusted ONLY here — supplies the full katakana form of 18
   metric-unit words (`瓩 → キログラム`, `竏 → キロリットル`,
   `糎 → センチメートル`, …; Unihan's own Kun romaji is truncated for
   7 of them, e.g. `瓸 HEKUTOGURA` for ヘクトグラム). Two fish kanji
   come from JMdict single-kanji entries only (`𩸽 → ほっけ`,
   `𩺊 → あら`). The 18 unit words are the ONLY katakana values in
   `readings.json` — everything else is hiragana.
5. **Skipped (744)** — kanji with no usable reading anywhere are left
   out rather than given an invented reading (see Limitations).

Verified: JSON reloads OK, no duplicate keys, no empty values, every
value matches `^[ぁ-んー]+$` (or katakana for the 18 unit words above),
every romaji value matches `^[a-z]+$`, keys sorted by codepoint, keys
identical between the two files, all 2,136 Joyo and all 869 hyogai
kanji from the old repo are covered, and a rebuild from scratch
reproduces byte-identical files.

## Rebuilding

```bash
python3 build_readings.py
# needs kanjidic2.xml.gz from SRMusic-JP-Index (same directory as its
# build_index.py) and JMdict_e.gz + Unihan_Readings.txt in this directory.
```

`JMdict_e.gz` (219,176 entries, SHA-256
`2ed08eb81fab44f91415452cf64ab0fb980d3db39614041635d3da3b7b61f6a5`)
was downloaded from
`http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz`
(the canonical EDRDG URL; use plain HTTP if the HTTPS endpoint returns
an empty reply behind a proxy). It is a build input only and is **not**
pushed to GitHub — the connector only pushes UTF-8 text files anyway.

`Unihan_Readings.txt` (Unicode 18.0.0, dated 2026-07-31) comes from
`Unihan.zip` at `https://www.unicode.org/Public/UNIDATA/Unihan.zip`.
Only `Unihan_Readings.txt` is needed (8.5 MB). Same rule as JMdict:
build input only, **not** pushed to GitHub.

## Honest limitations

- **One default reading per kanji cannot be right in every word.**
  `生` has many readings: this file says `せい`, but `生きる`
  (`いきる`), `生まれる` (`うまれる`), `生える` (`はえる`) and `生`
  (`なま`, "raw") are all correct in their own words. Words like that
  need word-level overrides (`lyric_overrides.json`), not this file.
- **Verb conjugations are not covered.** `言って` → `いって` needs a
  word/phrase pre-reading; looking up `言` alone gives `いう`.
- **JMdict's top priority entry is sometimes a special reading.**
  Verification found e.g. `彼 → あれ`, `先 → さっき`, `抑 → そもそも`,
  `頓 → ひたすら`, `電 → いなずま`, `古 → いにしえ` — all fixed by
  manual corrections or the adverb filter, but the same class of error
  may hide in unreviewed entries. If you find a wrong default, add it
  to `MANUAL_CORRECTIONS` in `build_readings.py` and rebuild.
- **744 kanji are missing** — 285 CJK Ext. B, 160 Ext. A, 12
  compatibility ideographs, and 287 others in the main CJK block.
  Breakdown of why they stay out: 3 are radicals whose only Unihan
  reading is the radical's name (`爫` "tsume-kanmuri", `犭`
  "kemono-hen", `牜` "ushi-hen" — a name, not a pronunciation); 723
  have only the legacy Unihan `kJapanese` field, which is proven
  unreliable (it lists 笑's readings for `关`, 開's readings for
  `开`, and dubious readings like `囍 → よし そうき`); 18 have no
  Unihan entry at all. None of them has any reading in KANJIDIC2 or
  JMdict either, so they were skipped instead of guessed. The app
  should fall through to Jisho/Google for those.
- **Not listening-tested.** This is a static index; the app integration
  (fetch + lookup order above) is still to be done and tested on a
  real device.
