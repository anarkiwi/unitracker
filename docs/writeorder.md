# Write order and hard restart

The method, the verified envelope physics, the orders, the order cover and the hard restart bound
are in vagrant-moses (`vagrant_moses.sidorder`, docs/sidorder.md there). It works on SID write
streams; these tools capture the streams of tunes and drive it over a corpus:

- `tools/writeorder.py`: each frame's event pattern per voice, and how each per-voice order of
  the kept writes changes it (fast, no emulation);
- `tools/ordercover.py`: the fewest orders that keep every frame's envelope exactly, and how
  tunes' hard restarts behave;
- `tools/solvertime.py`: the solver's time on captured tunes as a multiple of real time;
- `tools/orderaudio.py`: one order's differences measured and rendered at a fixed pitch;
- `tools/samecall.py`: registers a player writes more than once in one call.

## Usage

```
python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--top K] [--out F.json]
python tools/ordercover.py TUNES.csv|TUNE.sid ... [--frames N] [--horizon N] [--out F.json]
python tools/orderaudio.py TUNES.csv|TUNE.sid ... [--order ORDER] [--wav DIR]
python tools/solvertime.py TUNES.csv|TUNE.sid ... [--per-player N] [--horizon N]
```

Writes come from the in-process emulator (`--backend jennings`, tunes with a play address) or the
sidtrace oracle (`--backend sidtrace`); `auto` picks per tune. The tools need
`vagrant-moses[sidorder]>=0.8.0` (in the `dev` extra).

The corpus covers are exact per frame; `vagrant_moses.sidspace` (vagrant-moses docs/sidorder.md)
shows that order within a frame only matters where the rate counter matches between writes, so
the canonical output is `sr,ad,ctrl` per gate segment.
