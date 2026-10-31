# GoatTracker 2 lift

`unitracker.lift.goattracker2` builds a format 2.5 document from pygoattracker's typed song
(`canonical_song` of a decompiled SID, or a typed song packed by `songimage`) and the flags of the
bound playroutine spec. No player is run to build it.

```sh
python -m unitracker.lift.goattracker2 TUNE.sid > tune.yaml
python -m unitracker.lift.goattracker2 --verify [--frames N] [--jobs J] TUNE.sid ...
python tools/lift_corpus.py NAMES.txt
```

## Mapping

| GoatTracker 2 | document |
|---|---|
| row of `tempo + 1` calls | step, beginning one call after the row's tick 0 |
| song start | `init` with `tempo:2`, phrase `p0:[-,- tempo:T]` |
| orderlist, repeat, transpose, restart | `voice:[...]`, repeats unrolled, `+t`, `loop:k` |
| pattern | phrase, one event per row |
| gate timer, hard restart, first wave | instrument `restart` program: fetch row, tick 0 row |
| wavetable | `ctrl` program (`wait`, `ctrl`, `note`) |
| pulse table | `pw` program, `{hold:n,pw:'v + s'}` modulation rows; `holds:{pw:[...]}` |
| filter table | `filter` program on `cut`, `res`, `route`, `mode`; `global` cells |
| commands 5-D | `ad`, `sr`, `ctrl`, `run`, `res`/`route`, `set.cut`, `vol` |
| commands E, F | `tempo:a/b`, `tempo:n`, `vtempo:n` |
| portamento, toneporta | `tick.freq` expressions |
| vibrato, instrument vibrato | `tick.freq` code templates with state `t`, `d` |
| wavetable note rows | `wave.freq` once binding |
| full ghosting, start state | `regs`, `lag`, first-tick cells |

## Refusals

Typed `Refusal(kind)`, raised before lifting: interrupt tunes (play address 0), multi-SID and
stereo, builds with `noeffects`, `directwave`, `livegate` or `nonormalspeed`, calculated speeds,
wavetable commands other than portamento and the one-shot cells, tempo recall with several funk
tables, gate-column notes, a pointer or jump onto a table jump row.

## Verification

`verify` plays the document with unitracker-player and compares its grid with the spec player's at
the standard note table; `lift` raises `Divergence` naming the first differing frame and register.
