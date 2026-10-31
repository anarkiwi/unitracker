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
| tick order of a call | `order:[events,ctrl,read,pulse,filter,freq,pw,cut]`: the row fetch (`read`, the restart's first row) runs after the note's events and the control program, as `done()` follows the tick-0 work and `waveexec`, so a fetch on the previous note's tick turns its gate off after that note gated on |
| `repeat` entries | a group `[p]xN` of the pattern |
| orderlist loop | `loop:k`; the loop body is written a second time only when the context it is re-entered in changes a phrase |
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
| commands E, F | `tempo:a/b`, `tempo:n`, `vtempo:n`; a funk table's two bytes as the row lengths the player's counter reload gives (`cadence`) |
| funk recall (F00, F01) | the table in force: the start state's where no table is loaded, else the one table every load names where a voice loads it in its first row and no voice ahead of it recalls in a first row |
| note that keeps the gate (legato instrument, no first waveform) | the gate is followed through the voice's rows (key off, key on, notes); where it is off, the note's event runs the instrument's wavetable with a first row `{gate:0,hold:0}` (`run:`), and an instrument whose fetch turns the gate off but sets none has that row in its own program |
| gate timers read from the live instrument (`livegate`) | the instrument's `restart`, as the live instrument is the one the last row fetch loaded; refused where a row change to a shorter timer fetches again |
| play routine flushing a ghost block | `lag` and `regs`, as a full ghost; refused where the routine writes registers of its own |
| portamento, toneporta | `tick.freq` expressions |
| vibrato, instrument vibrato | `tick.freq` code templates with state `t`, `d`, `ln` |
| calculated speed | code templates with state `sl`, `sh`: `F(N + 1) - F(N)` shifted by the row's count, `N` the last note loaded (`ln`) |
| `noeffects`, `nonormalspeed` builds | no tick effects; the speedtable rows as the build reads them |
| wavetable note rows | `wave.freq` once binding |
| full ghosting, start state | `regs`, `lag`, first-tick cells. The song-start call loads each control register as `wave & gate` from the wave the reset zeroes, so the second frame differs from the ghost the init left where a control register is not already that; the document then lags by one call (`lag:1`), the init events carry the control cells, and the first-tick cells move to a middle row of `p0` one call on |
| channel variables the song start leaves | the song start zeroes `resetlen` + 1 bytes from `songptr` and `initchn` sets `instr`, `counter` and the tempo; `vibtime`, `vibdelay` and `lastnote` keep what the init left. A once binding on the init event loads those that command 0's vibrato reads where they differ from the document's (`t` 0, `d` the first instrument's delay, `ln` 0) |
| pointer or jump onto a jump row | the executor runs the landed row as an ordinary row of its bytes `$FF,n` and goes on from the row after it; a pulse table's is a pulse set of the width `$F00` + `n` (`gt2tables`), stated as an appended row followed by a jump onward |

## Refusals

Typed `Refusal(kind)`, raised before lifting; `detail` names the construct.

| kind | the construct |
|---|---|
| `interrupt` | play address 0 |
| `multi-SID` | stereo, or more than three channels |
| `SID address` | a player writing a SID elsewhere than `$D400` |
| `wrapper` | a play routine that writes SID registers other than as copies of its ghost block (probed over its first calls; the one in the corpus adds a cutoff sweep, a 16-bit sum stepped each call, and a volume xor with a self-modified operand) |
| `build` | `directwave`, a pulse step other than 1 and 2, a voice layout or control overlay, a tempo under 2; `livegate` where a row change to a shorter gate timer fetches again |
| `start state` | a note, command or table program the song start leaves running: `newnote`, `newfx`, `newparam`, `waveptr` or `pulseptr` outside the zeroed bytes and nonzero |
| `layout` | a speedtable row the song uses that the player reads as other bytes than the typed song holds (none in the corpus since pygoattracker 0.10.2 binds the speedtable) |
| `restart overlap` | a note whose restart reaches back before the previous note's tick, which the player never fetches |
| `first waveform` | a gate-keeping note off, whose event also sets a table (`run` is taken) |
| `noeffects vibrato` | instrument vibrato in a `noeffects` build: the player runs effect 0 each tick with the vibrato parameter of the live `instr`, which the row fetch loads before the note starts, including for a voice's first note and an instrument-only row, which have no restart row to rebind at the fetch |
| `pointer onto a jump row` | a pointer or jump an instrument or command reaches that lands on a wavetable or filter table jump row (the tempo command F, a filter set of every mode bit: no typed row), on the last row of a table, or in a table that runs on past its last row beside such a landing |
| `funk tempo` | a funk table whose row never ends (a byte of 0, or a pair whose reloads keep a negative counter), or whose rows do not alternate from the entry |
| `funk tempo recall` | tempo recall where the table in force is not one: several tables loaded, or none loaded in a first row |
| `pattern command`, `gate note`, `timing mark in a build without author info` | rows the document cannot state |

## Reference

The reference is the spec player of pygoattracker, which plays the tune's own play routine around
the player; a tune whose player the song does not describe is refused rather than lifted.

## Verification

`verify` plays the document with unitracker-player and compares its grid with the spec player's at
the standard note table; `lift` raises `Divergence` naming the first differing frame and register.

## Objects and sizes

Every named object of a document has the native object it transliterates
(`invariants.provenance`): an instrument, its restart program, a table entry point, a pattern; two
objects never share an origin, a value or go unnamed. A phrase carries the entry context
(instrument and, where an instrument keeps the gate, the gate) only where its pattern lifts to
more than one phrase, which happens where the player behaves differently: a command-0 row latches
the vibrato of the instrument carried in. A wavetable's gate-off variant is its own program with
the context `quiet`. Orderlist entries are bounded per voice by `native_counts`: its entries, the
entry starting the voice, and its loop body once more. `tests/lift_sizes.json` holds each
fixture's song bytes; `grown` must be empty.
