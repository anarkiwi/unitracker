# unitracker

Direct lifters of GoatTracker 2, SID-Wizard, defMON and Music Assembler tunes to the generic C64 SID tracker document of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar), which names no player and
which [unitracker-player](https://github.com/anarkiwi/unitracker-player) plays, and a generic
optimiser over any such document. Each lifter transliterates its native player literally into
the document model; the optimiser is the one player-agnostic pass that makes it smaller, every
edit proved to keep the register grid.

| doc | content |
|---|---|
| [docs/direct/goattracker2.md](docs/direct/goattracker2.md) | the GoatTracker 2 transcription from pygoattracker's playroutine (`python -m unitracker.direct.goattracker2`) |
| [docs/direct/sidwizard.md](docs/direct/sidwizard.md) | the SID-Wizard transcription from pysidwizard's playroutine (`python -m unitracker.direct.sidwizard`) |
| [docs/direct/defmon.md](docs/direct/defmon.md) | the defMON transcription from pydefmon's emulated replay (`python -m unitracker.direct.defmon`) |
| [docs/direct/musicassembler.md](docs/direct/musicassembler.md) | the Music Assembler transcription from pymusicassembler's playroutine (`python -m unitracker.direct.musicassembler`) |
| [docs/optimiser.md](docs/optimiser.md) | the generic optimiser: its driver, guarantee and passes (`python -m unitracker.optimiser`) |
| [docs/optimiser.md#passes](docs/optimiser.md#passes) | the passes: defaults, merge, transposition folding, the legato snap, templates and their structural names, hoist (`python tools/passes_eval.py`) |
| [docs/optimiser.md#inline](docs/optimiser.md#inline) | the optimiser's inline forms (`--inline single`, `--inline all`): definitions written at their uses |
| [docs/optimiser.md#lossy-mode](docs/optimiser.md#lossy-mode) | the lossy mode: code modulators replaced by fitted expressions within a tolerance per cell (`--lossy`) |
| [docs/learnability.md](docs/learnability.md) | how well language models learn documents: per form and tokenizer from scratch, and Qwen2.5 fine-tuned, in held-out bits per song tick and playable samples (`python tools/learn.py`) |
| [docs/tokenization.md](docs/tokenization.md) | how documents tokenize, by form, tokenizer and lexical category; printer changes and their savings; a document BPE (`python -m unitracker.optimiser.tokenizer`) |

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid [-o OUT.yaml] [--subtune N] [--optimise [--ticks T] [--lossy] [--inline MODE]]
```

It detects the tune's format (pysidtracker) and writes its direct lifter's document, optimised
with `--optimise` (with `--lossy`, code modulators first replaced by fitted expressions).

```python
from unitracker import cli

cli.lift(raw, subtune)            # raw: the .sid bytes; the generic document's text
cli.verify(raw, subtune, frames)  # the cells where the document's grid differs from the native
```

`tools/corpus.py:render` is the renderer for pysidtracker's survey: it lifts each subtune and
plays its document on unitracker-player, compared with the sidtrace oracle:

```sh
python -m pysidtracker.survey find $HVSC --out out/survey/tunes.csv --player 'goattracker|wizard|defmon'
PYTHONPATH=tools python -m pysidtracker.survey run $HVSC out/survey/tunes.csv --work out/survey --render corpus:render
python -m pysidtracker.survey report out/survey/results.csv
```

## Triage

Reuse these before writing a new tool; extend one rather than adding a near copy.

| question | tool |
|---|---|
| does each tune lift to a document that plays its native player's grid, and why not (by cause) | `python tools/corpus.py check [TUNE.sid\|LIST.csv ...] [--out DIR] [--frames N] [--player RE] [--timeout S] [--multi-sid]` (default: the reference tunes; multi-SID tunes, by header or a GoatTracker build past three voices, excluded) |
| why do a `check` table's differing tunes diverge (standard pitch, one-tick lag, start registers, register groups), with each GoatTracker 2 build's flags | `python tools/divergence.py out/check/results.csv [--frames N] [--jobs J]` (writes `classes.csv` beside it) |
| where does one lifter's document differ from its native player? | `python -m unitracker.direct.{goattracker2,sidwizard,defmon,musicassembler} --verify TUNE.sid ... [--frames N] [--diff RUNS] [--jobs J]` |
| lift failures over HVSC, by cause | `pysidtracker.survey` with `corpus:render` (above) |
| the native player's register grid | `pysidtracker.find_format(raw).play(raw, subtune)` |
| what does the lossy mode replace, save and cost per tune; how does it sound? | `python tools/lossy_eval.py DOC.yaml ... [--ticks N] [--wav DIR]` |
| each tune's lifted and optimised forms, cached, for a corpus study | `python tools/corpus.py forms TUNES_DIR\|LIST.csv [--out out/tokenize] [--forms ...] [--timeout S] [--multi-sid]` |
| tokens per form, tokenizer and lexical category; what each printer transform saves | `python tools/tokens.py [out/tokenize] [--forms ...] [--encoders ...]` |
| how large is each lifted document against its native tune (bytes, tokens, xz per native and song-data byte), which songs blow up most per player, and in which section | `python tools/liftsize.py CORPUS LIST.csv ... [--hvsc DIR] [--top N] [--out SIZES.json]` |
| which sounds of each document are percussive (one or two pitches, over and over), and how much each part repeats where a voice interleaves them | `python tools/parts.py DOC.yaml ... [--ticks N] [--out PARTS.json]` |
| what starts each program (a note, an event's or a row's `run:`, a restart), which starts are replaced before they play a row, what binds and zeroes each modulator's phase, and which programs do nothing | `python tools/triggers.py DOC.yaml\|LIST.txt ... [--ticks N] [--out TRIGGERS.json]` |
| does a model's sampled text keep to the document grammar; mask sampling to it | `python tools/constrain.py {check,sample,bench,hazards} TOKENIZER [DOCS...]` |
| how learnable is each form and tokenizer; can a fine-tuned LLM write tunes? (GPU, in Docker, detached) | `python tools/learn.py {launch,status,run} [--preset smoke\|full] [--set KEY=VALUE ...]` |
| do the docs' links resolve; which lines did coverage lose? | `python tools/maint.py {links,coverage BEFORE.json AFTER.json}` |
| which generator (semitone offset, `clock_hz`, rounding) reproduces each tracker's native note table, and which entries it misses | `python tools/notetables.py [--tune TRACKER=PATH ...] [--json OUT]` |
| a small synthetic tune for a test | `tests/conftest.py`: `gt_tune`, `table`, `hvsc_tune` |

## Layout

`src/unitracker/`: `direct/` (one transcription per tracker, its playroutine code as generic
code modulators, and the command line they share), `optimiser/` (the generic optimiser), `cli.py`
(`unitracker-lift`). `cli` imports `direct` and `optimiser`, which import neither each other nor
`cli`; `lint-imports` checks it (`[tool.importlinter]` in `pyproject.toml`).

## Test

```sh
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tunes are fetched into .tunecache/
```
