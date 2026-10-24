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
| [docs/optimiser.md](docs/optimiser.md) | the generic optimiser over any canonical document: its driver, guarantee and passes (`python -m unitracker.optimiser`) |
| [docs/porting.md](docs/porting.md) | bringing another playroutine to unitracker |
| [docs/direct/goattracker2.md](docs/direct/goattracker2.md) | a standalone GoatTracker 2 transcription from pygoattracker's playroutine (`python -m unitracker.direct.goattracker2`) |

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

`tools/corpus.py:render` is the renderer for pysidtracker's survey: it lifts each subtune and
plays its generic document on unitracker-player; `--format auto` plays the tune's installed
`pysidtracker` format's native player instead. Both are compared with the sidtrace oracle (lift
failures are `library_error` rows):

```sh
python -m pysidtracker.survey find $HVSC --out out/survey/tunes.csv --player 'goattracker|wizard|defmon'
PYTHONPATH=tools python -m pysidtracker.survey run $HVSC out/survey/tunes.csv --work out/survey --render corpus:render
python -m pysidtracker.survey report out/survey/results.csv
python -m pysidtracker.survey file --format auto TUNE.sid --frames 3007   # one tune; tools/corpus.py:render for its document
```

## Triage

Reuse these before writing a new tool; extend one rather than adding a near copy.

| question | tool |
|---|---|
| does each tune lift to a generic document that plays its native player on unitracker-player, and why not (by cause; `--against` an earlier run) | `python tools/corpus.py check [TUNE.sid\|LIST.csv ...] [--out DIR] [--forbid WORD ...] [--diff] [--against RESULTS.csv] [--only CAUSE]` (default: the reference tunes) |
| does the lifter's song replay its native grid on the VM, without expressions | `python tools/corpus.py check LIST.csv --plain --ticks 3007 [--registers 2,3]` |
| does a lifter change alter any tune's output? | `python tools/corpus.py check ... --out DIR [--plain]` before and after, then `diff -r` |
| a random lifted sample of generic documents, balanced over the three players | `python tools/corpus.py check tunes.csv --sample N [--seed S] [--subtunes 1] --out DIR` (from the survey's `find` csv) |
| where (on which ticks, registers and values) does a generic document differ from the native grid? | `python tools/corpus.py grid DOC.yaml TUNE.sid [--ticks N] [--json [PATH]] [--grids DIR] [--explain native\|6502\|vm]` (exit 1 on a difference; `--grids` for `python -m pysidtracker.gridcmp`; `--explain` why, [docs/porting.md](docs/porting.md#2-transcribing-the-replay)) |
| what the canonical passes find, prove and refuse over many tunes | `python tools/corpus.py canonical TUNE.sid ... [--jobs J] [--workers N] [--out DIR]` |
| how documents use the vocabulary, and which native commands tunes play | `python tools/corpus.py census DOC.yaml\|TUNE.sid ... [--codes 14,15] [--json OUT.json]` |
| lift failures over HVSC, by cause | `pysidtracker.survey` with `corpus:render` (above) |
| the fewest fixes for where lifted documents part from the native player over a corpus, each a strict xfail explained by whydiff (`tests/pathdiff.json`) | `python -m pysidtracker.pathdiff {build,rank,xfails} ... --right unitracker.lift.backends:pathdiff_lift --compare onset` ([regenerating](docs/porting.md#corpus-divergences)) |
| the native player's register grid | `pysidtracker.find_format(raw).play(raw, subtune)`; the 6502 oracle grid is `tests/conftest.py:oracle` |
| why did a modulator not become an expression? | `python tools/modulators.py occurrences TUNE.sid [--unit U] [--cells c1,c2]`, `unitracker-modfit exprs` |
| on which ticks does the native unit hold each expression its generic modulators play? | `python tools/modulators.py firings TUNE.sid [--cell pw:0] [--ticks A:B] [--timed]` |
| what writes a unit's cell, and which starts inherit its value | `python tools/modulators.py writers goattracker2 sidwizard defmon` |
| which expressions two runs write differently, proved equal or not | `python tools/modulators.py exprs OLD_DIR NEW_DIR [--bits B]`; display forms: `modulators.py display record\|compare` |
| does a playroutine's modulation prove? | `unitracker-modfit {fit,poly,command,keywords,generic,sweeps,orbit}` ([docs/modulation/modfit.md](docs/modulation/modfit.md)) |
| where does a lift, conversion or optimisation spend its time? | `python tools/profiling.py {lift,convert,optimise} TUNE.sid [--profile OUT.prof]` |
| do the docs' links resolve; do the playroutine docs' tables match; which lines did coverage lose? | `python tools/maint.py {links,tables [--write],coverage BEFORE.json AFTER.json}` |
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
