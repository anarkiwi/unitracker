# unitracker

A generic tracker song format for the C64 SID, a GoatTracker 2 lifter, and a generic
player. A song is a YAML document holding the music (tracks, phrases, tables, records)
and the playroutine that plays it (integer cells, bounded accumulators, event handlers). The
player has no player-specific knowledge, and its register writes are flushed in one
canonical order.

Format specification: [docs/format.md](docs/format.md).

## Install

```sh
pip install -e ".[dev]"
```

## Use

```sh
unitracker-gt TUNE.sid -o song.yaml [--subtune N]          # lift a GoatTracker 2 .sid
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
HVSC=/path/to/C64Music pytest -n auto      # without $HVSC the tune is fetched into .tunecache/
```
