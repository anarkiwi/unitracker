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

## Subtunes

A subtune's init may point the voices at other orderlists and patterns than the file's, so the lift
reads both from the memory init left, through the pointer tables the binding names.

## Mapping

| Music Assembler | document |
|---|---|
| orderlist entry, transpose, repeat | `voice:[...]`, `+t`, `xN`; `$FF` and `$FD n` as `loop:k` |
| `$FC t` | skipped where `t` is the tempo; any other is refused |
| `$FE` silence | 127 one-row events then one of 129 rows (the row counter parses 128 row calls, plays 128), looped; the gate off by `m.ctrl`: once, or, under a program that stops and is still writing `ctrl`, `ite(phi >= T, v & 254, v)` |
| pattern row | phrase event, one per row |
| note | the note, its instrument, `vtempo` |
| tied note (`$40`) | `legato`, the frequency and mask rebound, no trigger |
| rest | `-`, `m.ctrl:'v & 254'` for the gate mask |
| hold, select | `-`; `.iK` and the sweeps' new steps for a select |
| instrument trigger | `ctrl` program: row `ad`, `sr`, `ctrl`, then the inner program |
| inner program row | read from the bytes the program pointer leads to; `ctrl:wave`, `freq:'F((n + p) & 127)'` (`F(p & 127)` absolute); `end`, `{loop:k}` |
| pulse flag `$40` or always | `pw:'ite(phi == 0, init, v + 257 * s)'`, rebound `'v + 257 * s'` on a select |
| pulse flag `$20` | code template `pong(p,s,k)`, `pongs(s,k)` after a select |
| instrument `$10` sweep | code template `arp(w,m)`, `arpl(m)` on a tied note, `arpz(m,p,b)` after a stopping program; `arpc(s,k,b)` sets its step, steps a phase and vibrato flag |
| note `$20` | slot `b` of `freq`: `F(n) + (phi + 1) * word`, or with an odd low step `ite(phi % 2 == 0, ..., v)` |
| note `$80` | `res`, `route`, `s<x>.cut:fset(...)!`, `c<x>.cut:fsweep(x)` |
| program row filter byte | `q<x>.cut:poke(u)!` |
| image filter sweep | `boot.cut:fseti(...)!` and `c<x>.cut:fsweep(x)` on the owner's init |
| first rows that play no note | `freq`, `pw`, `m.ctrl` constants of the cells init left; a trigger of the image where its note control has no latch; a vibrato the image runs, `b.freq` from the output cells and step bytes init left |

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
| `play routine` | the play address reaches the play entry through more than `JMP abs` (see [Wrappers](#wrappers)) |
| `native` | the bound spec still holds a `native` statement |
| `multi-SID` | several play entries |
| `SID mirror`, `voice dispatch`, `layout` | cells the model does not keep apart, or apart from where the spec has them |
| `arpeggio code`, `relocated arpeggio operands` | a comparing variant of the sweep, operands off the cells the spec names |
| `init` | a first play call that reads no row; a pulse sweep or an arpeggio trigger running from the image |
| `orderlist` | a restart inside an entry, a list that does not reach a pattern or settle |
| `tempo change` | an orderlist `$FC` that changes the tempo (see [Tempo changes](#tempo-changes)) |
| `legato filter command` | a tied note carrying a filter command |
| `silence` | a silence in a build whose rows leave the row counter |
| `program jump`, `pattern`, `instrument` | a row jump bound apart from the cursor, a pattern the song lacks |

## Verification

`verify` plays the document with unitracker-player and compares its grid with the spec player's at
the standard note table (`fmt.spec_player(tune, notes=True)`, index `i` of `freq_lo`/`freq_hi` is
standard note `i`); `lift` raises `Divergence` naming the first differing frame and register.
`python tools/lift_corpus.py` lifts any registered format's tunes and counts exact, refused (by
class), diverged and erroring ones.

### Programs

`musicassembler.play`'s `program` proc reads three bytes a row through the pointer of the slot
`inner[prog]`, the marker at `y + 3` (`$FE` stops, `$FF` restarts, `$FD` jumps where the build binds
it, anything else is the next row) and moves the cursor `y` as a byte. A slot whose table is empty
is the same read: the bytes the pointer leads to are its rows, so a pointer at a marker reads the
marker as the wave of a first row. `Lift.walk` follows the cursor through those bytes, a jump
to a row past the marker included, and ends at a stop or at a cursor it has visited.

### Silence

`parse` returns at `pid == SILENCE` after `gate_ctrl &= 0xFE`, so a parse call writes nothing, and
the next `program` call rewrites `ctrl` from the wave. A stopping program that has rows left
after a parse therefore overwrites the cleared gate, which stays clear from the first parse that
follows the program's last row. The gate is cleared by `m.ctrl` bound `ite(phi >= T, v & 254, v)`,
`T` the calls (parse calls aside) before that parse (`Lift.gate_start`), counted from the calls
since the trigger the lift carries through the voice's phrases.

## Object counts

`native_counts` bounds the objects of each kind a document may hold by the song alone, and
`invariants.check` raises where a lift exceeds it. A pattern changes the state a voice carries
(instrument, last trigger, vibrato, rest mask) to what its last row sets, so a voice's list is
lifted at most twice: from the state init left and from the state the list leaves. An entry with a
repeat plays its first run from the entering state and the rest from the state the first left. Per
voice, `phrases` are at most 2 passes x (1 + 1 if the entry repeats) for each real entry, plus 2
for a parked voice; `song` entries at most 4 per real entry (2 passes x 2 runs) plus 2 for a parked
voice; instruments and `ctrl` programs at most one per instrument reached (3 where its program
pokes the filter, the slot being the voice's) plus one per voice for a trigger of the image; `pw`
bindings one per instrument. `tools/lift_corpus.py` prints each tune's lifted and native counts
and the document bytes against the song data bytes.

## Wrappers

A play address that is not the play entry is a wrapper that runs the player's code on some of
its calls. Both corpus wrappers (`Raccoon_intro`, `Quadravision_4`) alternate: an even call
jumps to the play entry, an odd call to an output-only routine at `$1000` that, for each voice
whose flag at `$14d2 + v` is non-zero, runs the voice's output and nothing else (no `clock`
step, no row). That routine is the `else` of the spec's `schedule` (`for v in
L.output_voices { voice[v]; }`), which a `calls:{frame:1,full:[1,0]}` reduced call states for
the voices it outputs: it runs the programs and slots and not the `events` and `read` phases.
The flags of `Raccoon_intro` are `0,1,0` and those of `Quadravision_4` `0,1,1`, nothing writes
them, and a voice whose flag is 0 changes its registers on every other frame only. A reduced
call runs every voice's programs and slots, and `calls` has no schedule per voice, no event
names the reduced calls (`hold` events are `step`, `last`, `read`, `note`, `legato`, `row` and
their `-k`), and a program or modulator cannot be told to skip a call; so the voices that sit
out the output-only calls cannot be held still, and the refusal stays.

## Tempo changes

An orderlist `$FC t` runs `mem[c] := t` for both `order_tempo` cells (`tempo_reload` and the
`tempo` counter) at the parse of the last row of the entry before it, so the interval to the
next row call is `t + 1` and every later one `t + 1` (`machine clock`), for every voice at once.
A voice's row of `d + 1` row calls then lasts the sum of the intervals of its row calls, which a
change by another voice splits. The document states a tempo per step, fixed when the step
begins, so the exact statement is a step per row call (`tempo`, `*(d+1)`). But the spec's
`voice.step` skips `output()` only on the parse call, while the `hold` events of a slot or a
program (`step`, `read`) fire on every step of a `*k` event and no event names the first step of
an event that carries no note (a rest, a hold, an instrument select); a program would pause on
every row call. The other exact statement gives each event the length its row calls add up to,
which depends on where the other voices' lists are; it needs a phrase per tempo regime and a pass
per joint period of the lists, past the object counts above. The refusal stays.
