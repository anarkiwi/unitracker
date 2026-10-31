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
| registers before the first call | `regs`: the tune's own init writes, then the song-init call's writes over them, `$D418` at the host's volume |
| channel variables the song-init call leaves | the first call's cells on the `init` events: `freq`, `ctrl`, `ad`, `sr`, the filter cells; `pw` through a `boot` slot after `pulse`, which the pulse hold events leave unvisited on that call |
| play routine calling the player `n` times a frame | `calls:{frame:n}` |
| orderlist, repeat, transpose, restart | `voice:[...]`, repeats unrolled, `+t`, `loop:k`; a loop entered with another instrument than the first pass is played twice |
| pattern | phrase, one event per row |
| gate timer, hard restart, first wave | instrument `restart` program: fetch row, tick 0 row |
| wavetable | `ctrl` program (`wait`, `ctrl`, `note`) |
| wavetable commands | the row's cell commands; `F1`-`F4` a once binding of `wave.freq`; a command row holds the continuous effect for its tick |
| pulse table | `pw` program, `{hold:n,pw:'v + s'}` modulation rows; `holds:{pw:[...]}` |
| filter table | `filter` program on `cut`, `res`, `route`, `mode`; a filter-set row stops the modulation; `global` cells |
| commands 5-D | `ad`, `sr`, `ctrl`, `run`, `res`/`route`, `set.cut`, `vol` |
| commands E, F | `tempo:a/b`, `tempo:n`, `vtempo:n` |
| portamento, toneporta | `tick.freq` expressions |
| vibrato, instrument vibrato | `tick.freq` code templates with state `t`, `d`, `ln` |
| calculated speed | code templates with state `sl`, `sh`: `F(N + 1) - F(N)` shifted by the row's count, `N` the last note loaded (`ln`) |
| `noeffects`, `nonormalspeed` builds | no tick effects; the speedtable rows as the build reads them |
| wavetable note rows | `wave.freq` once binding |
| full ghosting, start state | `regs`, `lag`, first-tick cells |

## Refusals

Typed `Refusal(kind)`, raised before lifting; `detail` names the construct.

| kind | the construct |
|---|---|
| `interrupt` | play address 0 |
| `multi-SID` | stereo, or more than three channels |
| `SID address` | a player writing a SID elsewhere than `$D400` |
| `wrapper` | a play routine that flushes a ghost block itself |
| `build` | `directwave`, `livegate`, a pulse step other than 1 and 2, a voice layout or control overlay, a tempo under 2 |
| `layout` | a speedtable row the song uses that the player reads as other bytes than the typed song holds |
| `restart overlap` | a note whose restart reaches back to the previous note's tick, where the document applies the earlier note's gate after the later note's rows |
| `first waveform` | a note with no first waveform after a gate-off, which keeps the gate off |
| `noeffects vibrato` | instrument vibrato in a `noeffects` build, which follows the instrument read |
| `pointer onto a jump row` | a pointer or jump an instrument or command reaches that lands on a table jump row, which the executors play as an ordinary row |
| `funk tempo` | a funk row that is a calculated speed |
| `funk tempo recall` | tempo recall with several funk tables |
| `pattern command`, `gate note`, `timing mark in a build without author info` | rows the document cannot state |

## Reference

The reference is the spec player of pygoattracker, which plays the tune's own play routine around
the player; a tune whose player the song does not describe is refused rather than lifted.

## Verification

`verify` plays the document with unitracker-player and compares its grid with the spec player's at
the standard note table; `lift` raises `Divergence` naming the first differing frame and register.
