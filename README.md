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

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid [-o OUT.yaml] [--subtune N] [--optimise [--ticks T]]
```

It detects the tune's format (pysidtracker) and writes its direct lifter's document, optimised
with `--optimise`.

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
| does each tune lift to a document that plays its native player's grid, and why not (by cause) | `python tools/corpus.py check [TUNE.sid\|LIST.csv ...] [--out DIR] [--frames N] [--player RE] [--timeout S]` (default: the reference tunes) |
| where does one lifter's document differ from its native player? | `python -m unitracker.direct.{goattracker2,sidwizard,defmon,musicassembler} --verify TUNE.sid ... [--frames N] [--diff RUNS] [--jobs J]` |
| lift failures over HVSC, by cause | `pysidtracker.survey` with `corpus:render` (above) |
| the native player's register grid | `pysidtracker.find_format(raw).play(raw, subtune)` |
| do the docs' links resolve; which lines did coverage lose? | `python tools/maint.py {links,coverage BEFORE.json AFTER.json}` |
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
