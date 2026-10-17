# unitracker

A generic tracker song format (v1.14) for the C64 SID, GoatTracker 2, defMON and
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

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid -o song.yaml [--subtune N] [--embed]   # lift a GoatTracker 2, defMON or SID-Wizard .sid
unitracker-play song.yaml --wav out.wav [--seconds 60] [--model 8580|6581]
unitracker-play song.yaml --check TUNE.sid                  # compare with the native player from each voice's first note
```

```python
from unitracker import songio
from unitracker.vm import SongPlayer

player = SongPlayer(songio.load("song.yaml"))
grid = player.render_grid(3000)            # (ticks, 25) SID register grid
```

`python tools/token_report.py song.yaml` counts a document's tokens (needs `tiktoken`).

## Test

```sh
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tunes are fetched into .tunecache/
```
