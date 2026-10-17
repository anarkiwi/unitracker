# unitracker

A generic tracker song format (v1.19) for the C64 SID, GoatTracker 2, defMON and
SID-Wizard lifters, and a generic player. A song is a compact YAML document: a header
naming its playroutine (integer cells, bounded accumulators, event handlers and modulator
units, from the library in `src/unitracker/playroutines/`), then one timeline that defines
each instrument, program, expression and phrase where it is first used. Rows and commands
share one vocabulary across players, pitch, pulse and filter modulations are integer
expressions in `v` and `φ` (`pw: 16*abs((φ - 32) % 128 - 64) + 512`, `!freq:@x1`), and
notes are named by real pitch. The player has no player-specific knowledge,
and its register writes are flushed in one canonical order.

Format specification: [docs/format.md](docs/format.md).

Modulation analysis and the proved conversion to expressions (`unitracker-modfit`):
[docs/modfit.md](docs/modfit.md).

Bringing another playroutine to unitracker: [docs/porting.md](docs/porting.md).

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid -o song.yaml [--subtune N] [--embed]   # lift a GoatTracker 2, defMON or SID-Wizard .sid
unitracker-play song.yaml --wav out.wav [--seconds 60] [--model 8580|6581]
unitracker-play song.yaml --check TUNE.sid                  # compare with the native player from each voice's first note
unitracker-optimise song.yaml -o smaller.yaml [--report orthogonality.md]  # same output, fewer tokens (docs/optimise.md)
```

```python
from unitracker.format import songio
from unitracker.player.vm import SongPlayer

player = SongPlayer(songio.load("song.yaml"))
grid = player.render_grid(3000)            # (ticks, 25) SID register grid
```

`python tools/token_report.py song.yaml` counts a document's tokens per section (needs the `dev` extras).

`tools/oracle_hvsc.py` holds the renderers for pysidtracker's survey: `render` lifts each
subtune, writes it, reads it back and plays it, `native` plays the backend's own player; both are
compared with the sidtrace oracle (lift failures are `library_error` rows):

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
| does a lifted song play its native player? | `unitracker-play song.yaml --check TUNE.sid`; in code `SongPlayer.mismatch` |
| where and on which register does it first differ? | `python tools/grid_diff.py song.yaml TUNE.sid --ticks 3007 [--from T --to T]` |
| do the three reference tunes still round-trip and match? | `python tools/reference_check.py OUTDIR [--forbid KEY ...]` |
| the native player's register grid | `lift.backends.native_player(raw)`; the 6502 oracle grid is `tests/conftest.py:oracle` |
| why did a modulator not become an expression? | `python tools/occurrences.py TUNE.sid [--unit freq\|pw\|cut]`, `unitracker-modfit exprs` |
| does a playroutine's modulation prove? | `unitracker-modfit {fit,poly,command,keywords,generic,sweeps,orbit}` ([docs/modfit.md](docs/modfit.md)) |
| tokens per section of a document | `python tools/token_report.py song.yaml` |
| dead state, duplicate forms, unreached vocabulary | `unitracker-optimise song.yaml --report orthogonality.md` ([docs/orthogonality.md](docs/orthogonality.md)) |
| lift failures over HVSC, by cause | `pysidtracker.survey` with `tools/oracle_hvsc.py` (above) |
| a small synthetic tune for a test | `tests/conftest.py`: `gt_tune`, `sw_fx`, `sw_table_byte`, `mini_song`, `table`, `hvsc_tune` |

## Layout

`src/unitracker/`: `format/` (reading and writing documents, the shared vocabulary, event folds),
`player/` (the generic player), `lift/` (detection and the three lifters), `modfit/` (proved
modulation conversion), `optimise/` (smaller documents), `analysis/` (format studies),
`playroutines/` (the library).

## Test

```sh
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tunes are fetched into .tunecache/
```
