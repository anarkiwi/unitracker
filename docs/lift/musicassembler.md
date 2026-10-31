# Music Assembler lift

`unitracker.lift.musicassembler` builds a format 2.5 document from pymusicassembler's song model and
the layout its playspec binding resolves for the tune's player. No player is run to build it: the
tables the player reads come from the song, the instruments, patterns and work cells the tune's
init left from the memory the binding names, and every behaviour from `specs/musicassembler.play`.

```sh
python -m unitracker.lift.musicassembler TUNE.sid > tune.yaml
python -m unitracker.lift.musicassembler --verify [--frames N] [--diff RUNS] [--jobs J] TUNE.sid ...
python tools/lift_corpus.py NAMES.txt --format musicassembler
```

## Time

A play call is a tick. Every pattern row is one event of one step, `vtempo:(d+1)(n+1)` ticks, so
a step starts exactly where the voice parses its row. A parse call writes nothing to the voice's
registers, so every slot of the voice's cells holds on `step` and the control programs pause on
it (`holds:{ctrl:[step]}`); what a note triggers happens on the next call, the first call the
slots and programs run. A row that stores its duration to a cell other than the voice's counter
(a build whose `dur_long`, `dur_short` or `dur_note` is apart from `dur`) lasts one row.

## Mapping

| Music Assembler | document |
|---|---|
| orderlist entry, transpose, repeat | `voice:[...]`, `+t`, `xN`; `$FF` and `$FD n` as `loop:k` |
| `$FC t` | skipped where `t` is the tempo; any other is refused |
| `$FE` silence | 127 one-row events then one of 129 rows (the row counter parses 128 calls, plays 128), looped; the gate off by `m.ctrl` |
| pattern row | phrase event, one per row |
| note | the note, its instrument, `vtempo` |
| tied note (`$40`) | `legato`, the frequency and mask rebound, no trigger |
| rest | `-`, `m.ctrl:'v & 254'` for the gate mask |
| hold, select | `-`; `.iK` and the sweeps' new steps for a select |
| instrument trigger | `ctrl` program: row `ad`, `sr`, `ctrl`, then the inner program |
| inner program row | `ctrl:wave`, `freq:'F((n + p) & 127)'` (`F(p & 127)` absolute); `end`, `{loop:k}` |
| pulse flag `$40` or always | `pw:'ite(phi == 0, init, v + 257 * s)'`, rebound `'v + 257 * s'` on a select |
| pulse flag `$20` | code template `pong(p,s,k)`, `pongs(s,k)` after a select |
| instrument `$10` sweep | code template `arp(w,m)`, `arpl(m)` on a tied note, `arpz(m,p,b)` after a stopping program; `arpc(s,k,b)` sets its step, steps a phase and vibrato flag |
| note `$20` | slot `b` of `freq`: `F(n) + (phi + 1) * word`, or with an odd low step `ite(phi % 2 == 0, ..., v)` |
| note `$80` | `res`, `route`, `s<x>.cut:fset(...)!`, `c<x>.cut:fsweep(x)` |
| program row filter byte | `q<x>.cut:poke(u)!` |
| image filter sweep | `boot.cut:fseti(...)!` and `c<x>.cut:fsweep(x)` on the owner's init |
| first rows that play no note | `freq`, `pw`, `m.ctrl` constants of the cells init left; a trigger of the image where its note control has no latch |

The `freq` cell has slots `a` (the word: note, program row or sweep), `b` (vibrato) and `c`
(sweep configuration); `ctrl` has `w` (writes) and `m` (the gate mask, an integer write binds
slot `w`); `cut` has per voice `s` (a filter command), `c` (the sweep) and `q` (a program poke),
in the player's voice order, so a sweep owner's step and a program's poke fall where the player
puts them.

## Refusals

Typed `Refusal(kind)`, raised before lifting where the tune's binding shows it:

| kind | |
|---|---|
| `interrupt` | play address 0: the tune runs from its interrupts, a digi among them |
| `play routine` | the play address reaches the play entry through more than `JMP abs` |
| `native` | the bound spec still holds a `native` statement |
| `multi-SID` | several play entries |
| `SID mirror`, `voice dispatch`, `layout` | cells the model does not keep apart, or apart from where the spec has them |
| `arpeggio code`, `relocated arpeggio operands` | a comparing variant of the sweep, operands off the cells the spec names |
| `init` | a first play call that reads no row; a sweep or an arpeggio trigger running from the image |
| `orderlist` | a restart inside an entry, a list that does not reach a pattern or settle |
| `tempo change` | an orderlist `$FC` that changes the tempo: a step is a row, so the length of every other voice's row would change |
| `legato filter command` | a tied note carrying a filter command |
| `silence` | a silence while a stopping program may run, or in a build whose rows leave the row counter |
| `program`, `program jump`, `pattern`, `instrument` | a program without rows, a jump into the bytes after its rows, a pattern the song lacks |

## Verification

`verify` plays the document with unitracker-player and compares its grid with the spec player's at
the standard note table (`fmt.spec_player(tune, notes=True)`, index `i` of `freq_lo`/`freq_hi` is
standard note `i`); `lift` raises `Divergence` naming the first differing frame and register.
`python tools/lift_corpus.py` lifts any registered format's tunes and counts exact, refused (by
class), diverged and erroring ones.
