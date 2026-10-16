# unitracker

A generic tracker song format for the C64 SID, GoatTracker 2 and defMON lifters, and a
generic player. A song is a YAML document holding the player build's tables, the
playroutine that plays it (integer cells, bounded accumulators, event handlers) and the
music (records, tracks, phrases). The
player has no player-specific knowledge, and its register writes are flushed in one
canonical order.

Format specification: [docs/format.md](docs/format.md).

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-lift TUNE.sid -o song.yaml [--subtune N]        # lift a GoatTracker 2 or defMON .sid
unitracker-play song.yaml --wav out.wav [--seconds 60] [--model 8580|6581]
unitracker-play song.yaml --check TUNE.sid                  # compare with the native player
```

```python
from unitracker import songio
from unitracker.vm import SongPlayer

player = SongPlayer(songio.load("song.yaml"))
grid = player.render_grid(3000)            # (ticks, 25) SID register grid
```

## Test

```sh
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tunes are fetched into .tunecache/
```
