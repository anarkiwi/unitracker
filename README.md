# unitracker

Lifters of GoatTracker 2, defMON and SID-Wizard tunes to the generic C64 SID tracker document of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar), which names no player and
which [unitracker-player](https://github.com/anarkiwi/unitracker-player) plays. Each lift ends by
verifying its document on unitracker-player against the native player's register grid. The
lifters decode each player by its playroutine declaration in `src/unitracker/playroutines/` and
convert native pitch, pulse and filter modulations to proved integer expressions in `v` and `φ`.

| doc | content |
|---|---|
| [docs/format/document.md](docs/format/document.md) | the generic document: its language and semantics, what the writer emits, an example |
| [docs/playroutines/](docs/playroutines/) | the playroutine [schema](docs/playroutines/schema.md), [lifting](docs/playroutines/lifting.md), and what each lifter decodes, covers and refuses: [goattracker2](docs/playroutines/goattracker2.md), [sidwizard](docs/playroutines/sidwizard.md), [defmon](docs/playroutines/defmon.md) |
| [docs/modulation/modfit.md](docs/modulation/modfit.md) | modulation analysis and the proved conversion to expressions (`unitracker-modfit`) |
| [docs/optimise.md](docs/optimise.md) | the optimiser's passes and guarantee |
| [docs/porting.md](docs/porting.md) | bringing another playroutine to unitracker |

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid -o doc.yaml [--subtune N] [--optimise]
```

It writes the generic document and exits nonzero, with the verdict on its last line, when the
document does not play the native grid on unitracker-player.

```python
from unitracker.lift import backends

lift = backends.lifter(raw)          # raw: the .sid bytes; subtune by default the header's
lift.run()
lift.document                        # the generic document's text
lift.verdict                         # unitracker-player against the native grid
backends.verify(text, raw, subtune)  # the verdict of any generic document
```

`tools/oracle_hvsc.py` holds the renderers for pysidtracker's survey: `render` lifts each subtune
and plays its generic document on unitracker-player, `native` plays the backend's player; both
are compared with the sidtrace oracle (lift failures are `library_error` rows):

```sh
python -m pysidtracker.survey find $HVSC --out out/survey/tunes.csv --player 'goattracker|wizard|defmon'
PYTHONPATH=tools python -m pysidtracker.survey run $HVSC out/survey/tunes.csv --work out/survey --render oracle_hvsc:render
python -m pysidtracker.survey report out/survey/results.csv
python -m pysidtracker.survey file tools/oracle_hvsc.py:native TUNE.sid --frames 3007   # one tune; :render for its document
```

## Triage

Reuse these before writing a new tool; extend one rather than adding a near copy.

| question | tool |
|---|---|
| does each tune of a survey or sample csv lift to a generic document that plays its native player on unitracker-player? | `python tools/generic_check.py [--csv sample.csv] [--hvsc DIR] [--jobs J] [--out DIR] [--diff DIR] [--against RESULTS.csv]` |
| on which ticks does the native unit hold each expression its generic modulators play? | `python tools/firing_schedule.py TUNE.sid [--cell pw:0] [--ticks A:B] [--timed]` |
| where (on which ticks, registers and values) does a generic document differ from the native grid? | `python tools/grid_diff.py DOC.yaml TUNE.sid [--ticks N] [--from T --to T] [--json PATH]` (exit 1 on a difference) |
| each reference tune's verify verdict, its differing registers and the machine-readable diff | `python tools/reference_check.py OUTDIR [--forbid KEY ...] [--json PATH]` |
| the native player's register grid | `lift.backends.native_player(raw)`; the 6502 oracle grid is `tests/conftest.py:oracle` |
| why did a modulator not become an expression? | `python tools/occurrences.py TUNE.sid [--unit freq\|pw\|cut]`, `unitracker-modfit exprs` |
| what writes a unit's cell, and which starts inherit its value | `python tools/cell_writers.py goattracker2 sidwizard defmon` |
| does a playroutine's modulation prove? | `unitracker-modfit {fit,poly,command,keywords,generic,sweeps,orbit}` ([docs/modulation/modfit.md](docs/modulation/modfit.md)) |
| what the canonical passes find, prove and refuse over many tunes | `python tools/canonical_tally.py TUNE.sid ... [--jobs J] [--workers N] [--out DIR]` |
| does a lifter change alter any tune's generic document? | `python tools/lift_snapshot.py OUTDIR SID ... [--plain]` before and after, then `diff -r` |
| do the docs' relative links resolve? | `python tools/doc_links.py` |
| lift failures over HVSC, by cause | `pysidtracker.survey` with `tools/oracle_hvsc.py` (above) |
| lift a tune list in parallel, results by cause (`--check`: the lifter's song replayed on its VM against the native grid) | `python tools/lift_tally.py TUNES.csv --root $HVSC --player SidWizard [--check 3007] [--registers 2,3]` |
| a random lifted sample of generic documents, balanced over the three players | `python tools/sample_tunes.py $HVSC tunes.csv OUTDIR [--count N --seed S]` (from the survey's `find` csv; `sample.csv` records each attempt) |
| how generic documents use the vocabulary over a corpus (events, commands, row keys, expression operators) | `python tools/vocab_census.py out/sample128/*.yaml [--json OUT.json]` |
| a small synthetic tune for a test | `tests/conftest.py`: `gt_tune`, `sw_fx`, `sw_table_byte`, `mini_song`, `table`, `hvsc_tune` |

## Layout

`src/unitracker/`: `playroutine/` (reading the playroutine declarations: the library,
record types, codecs, tempo, expressions), `document/` (the generic document writer and its
event folds), `player/` (the lifter's VM, its analysis engine, and `oracle`: unitracker-player
with each voice's first note), `lift/` (detection, the `Lift` stages and the three lifters),
`modfit/` (proved modulation conversion), `optimise/` (the optimiser), `analysis/` (format
studies, the `exprs` report and `unitracker-modfit`), `cache.py` (content-keyed caching and the
process pool), `playroutines/` (the playroutine YAMLs).

Each layer imports only those below it, `cli | analysis` > `lift` > `optimise` > `modfit` >
`document` > `player` > `playroutine` > `cache`, with no cycles between sibling modules;
`lint-imports` checks both contracts (`[tool.importlinter]` in `pyproject.toml`).

## Test

```sh
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tunes are fetched into .tunecache/
```
