# unitracker

A generic tracker song format for the C64 SID, GoatTracker 2 and defMON lifters, and a
generic player. A song is a compact YAML document in one player-independent vocabulary:
register-named keys, real-pitch note names, named commands, and every effect as a
bounded accumulator spec. A header names the song's playroutine (sequencing only, from
the library in `src/unitracker/playroutines/`), then one timeline defines each
instrument, program, spec and phrase where it is first used. The player runs the
accumulators itself, and flushes register writes in one canonical order.

Format specification: [docs/format.md](docs/format.md).

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid -o song.yaml [--subtune N] [--embed]   # lift a GoatTracker 2 or defMON .sid
unitracker-play song.yaml --wav out.wav [--seconds 60] [--model 8580|6581]
unitracker-play song.yaml --check TUNE.sid                  # compare with the native player
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
