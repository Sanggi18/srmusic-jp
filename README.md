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
| `readings.json` | ~235 KB | **12,338 entries**: `{kanji: hiragana}`, keys sorted by Unicode codepoint |
| `readings_romaji.json` | ~196 KB | Same 12,338 keys, Hepburn romaji values (`{"私": "watashi"}`) |
| `build_readings.py` | — | Reproducible build script (re-run gives byte-identical files) |
| `JMdict_e.gz` | 10.6 MB | Word-level source, **kept locally only — not pushed** (see below) |

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

1. **Manual lyric corrections (50)** — win over everything. These exist
   because the automatic steps get them wrong, e.g. `私 → わたし`
   (not `わたくし`), `愛 → あい` (not `まな` / `いとしい`),
   `言 → いう` (not `げん`), `電 → でん` (not `いなずま`),
   `生 → せい` (not `なま` / `いきる`), `本 → ほん` (not `もと`).
2. **JMdict single-kanji words (1,728)** — entries whose kanji element is
   exactly that one kanji, preferring priority tags
   (`ichi1`, `news1`, `nf01`–`nf24`, `spec1/2`). Adverb-only entries are
   skipped (e.g. `数 → しばしば` "often" and `熟 → つくづく` "deeply"
   are adverbs, never a kanji's default reading).
3. **KANJIDIC2 fallback (10,560)** — first kun reading (okurigana `.`
   and bound `-` markers stripped, e.g. `み.る` → `みる`), else first
   on reading converted to hiragana.
4. **Skipped (770)** — kanji with no usable reading anywhere are left
   out rather than given an invented reading (see Limitations).

Verified: JSON reloads OK, no duplicate keys, no empty values, every
value matches `^[ぁ-んー]+$`, keys sorted by codepoint, all 2,136 Joyo
and all 869 hyogai kanji from the old repo are covered, and a rebuild
from scratch reproduces byte-identical files.

## Rebuilding

```bash
python3 build_readings.py
# needs kanjidic2.xml.gz from SRMusic-JP-Index (same directory as its
# build_index.py) and JMdict_e.gz in this directory.
```

`JMdict_e.gz` (219,176 entries, SHA-256
`2ed08eb81fab44f91415452cf64ab0fb980d3db39614041635d3da3b7b61f6a5`)
was downloaded from
`http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz`
(the canonical EDRDG URL; use plain HTTP if the HTTPS endpoint returns
an empty reply behind a proxy). It is a build input only and is **not**
pushed to GitHub — the connector only pushes UTF-8 text files anyway.

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
- **770 kanji are missing** — 288 CJK Ext. B, 162 Ext. A, 12
  compatibility ideographs, and 308 others in the main CJK block
  (mostly simplified-Chinese forms like `关`/`开`, radicals/components
  like `亻`/`犭`, and archaic variants). They have no reading at all in
  KANJIDIC2 or JMdict, so they were skipped instead of guessed. The app
  should fall through to Jisho/Google for those.
- **Not listening-tested.** This is a static index; the app integration
  (fetch + lookup order above) is still to be done and tested on a
  real device.
