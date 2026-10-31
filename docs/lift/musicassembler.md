# Music Assembler lift

`unitracker.lift.musicassembler` builds a format 2.5 document from pymusicassembler's song model
(`pymusicassembler.parse`: each subtune's orderlists, patterns, instruments and inner programs as
its init leaves them) and `Song.init_state`, which carries the memory init left, the playspec
binding (`found`, `spec`) and the work cells the spec reads (`cell`, `voices`). No player is run to
build it: every behaviour comes from `specs/musicassembler.play`.

`unitracker.lift.macore` holds the typed refusals, `Core` and the refusals a tune's layout shows;
`macode` the 6502 modulators.

```sh
python -m unitracker.lift.musicassembler TUNE.sid > tune.yaml
python -m unitracker.lift.musicassembler --verify [--frames N] [--diff RUNS] [--jobs J] TUNE.sid ...
python tools/lift_corpus.py NAMES.txt --format musicassembler
```

## Time

A play call is a tick. `machine clock` gives every voice one row clock: a row call every
`tempo_reload + 1` calls, so the document states a global `tempo:R+1` and a step is a row call. A
pattern row of duration `d` lasts `d + 1` row calls, an event of `*(d+1)` steps. A voice's `parse`
call, the first call of its row, writes nothing to the voice's registers, so every slot of the
voice's cells holds on the hold event `event` (the first tick of each event, not of the later
steps of a `*k`) and the control programs pause on it (`holds:{ctrl:[event]}`); what a note triggers
happens on the next call, the first call the slots and programs run. A row that stores its duration
to a cell other than the voice's counter (a build whose `dur_long`, `dur_short` or `dur_note` is
apart from `dur`) lasts one step.

## Subtunes

A subtune's init may point the voices at other orderlists and patterns than the file's; the model
of `pymusicassembler.parse(raw, subtune)` holds the ones init left, and `core_of_sid(raw, subtune)`
lifts that model.

## Model gaps

The lift reads the memory (`init_state.window`) only where the spec reads bytes the model does not
carry:

| read | model gap |
|---|---|
| `Lift.record(k)` past the file's end | the model reads zero where the file stores no record; the player reads the RAM the machine holds |
| `Lift.walk` beyond a program's bytes | the cursor walks past an inner program's rows (a jump to a row after its marker, a slot with no program) |
| `Lift.events` of an empty pattern | the model ends a pattern at its first `$FF`; the player reads on past an empty pattern's |
| `_entered`, `Lift.header` registers | the play routine's code and the SID registers are not song data |

## Mapping

| Music Assembler | document |
|---|---|
| orderlist entry, transpose, repeat | `voice:[...]`, `+t`, `xN`; `$FF` and `$FD n` as `loop:k` |
| `$FC t` | `tempo:t+1` on the last row of the preceding entry's last run; none where every `$FC` keeps the tempo |
| `$FE` silence | 127 one-row events then one of 129 rows (the row counter parses 128 row calls, plays 128), looped; the gate off by `m.ctrl`: once, or, under a program that stops and is still writing `ctrl`, `ite(phi >= T, v & 254, v)` |
| pattern row | phrase event, one per row |
| note | the note, its instrument, `*(d+1)` |
| tied note (`$40`) | `legato`, the frequency and mask rebound, no trigger |
| rest | `-`, `m.ctrl:'v & 254'` for the gate mask |
| hold, select | `-`; `.iK` and the sweeps' new steps for a select |
| instrument trigger | `ctrl` program: row `ad`, `sr`, `ctrl`, then the inner program |
| inner program row | read from the bytes the program pointer leads to; `ctrl:wave`, `freq:'F((n + p) & 127)'` (`F(p & 127)` absolute); `end`, `{loop:k}` |
| pulse flag `$40` or always | `pw:'ite(phi == 0, init, v + 257 * s)'`, rebound `'v + 257 * s'` on a select |
| pulse flag `$20` | code template `pong(p,s,k)`, `pongs(s,k)` after a select |
| instrument `$10` sweep | code template `arp(w,m)`, `arpl(m)` on a tied note, `arpz(m,p,b)` after a stopping program; `arpc(s,k,b)` sets its step, steps a phase and vibrato flag (on the instrument with `b` 0, on a vibrato or tied note with `b` its flag), `arpk(s,k)` its step and steps a phase on an instrument select |
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
| `orderlist` | a restart inside an entry or onto a `$FC`, a leading `$FC` the init does not pass, a list that does not reach a pattern or settle |
| `legato filter command` | a tied note carrying a filter command |
| `silence` | a silence in a build whose rows leave the row counter; a silence under a stopping program in a tune whose `$FC` changes the tempo |
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

## Provenance and size

`invariants.provenance` holds the document to the origins `Lift.names.origins()` declares: every
named instrument, `ctrl` program and phrase has an `Origin(native, context)`, `native` the model
object (`("instrument", k)`, `("pattern", pid)`), none repeats another's origin or value and none
is written unnamed. `context` is set only for a native object that lifts to more than one record:
the voice and the entry state the walk reached it in (instrument, last trigger, rest mask, image)
and the tempo command that follows it. `invariants.check` bounds each voice's song entries by its
orderlist: each real entry once, its repeats counted once, and the two a parked voice loops.

The walk of a voice follows its orderlist from the state init left until it returns to an entry in
a state it has been in; the visits are then reduced to the shortest cycle (`Lift.reduced`), so a
state the player carries and the document needs not at all does not lengthen the song.

State the player carries stays out of the phrases: an instrument sets its sweep configuration
(`c.freq: arpc` in the instrument), the voice's image and mask go to the init event, and the vibrato slot is stopped
on the first note of a phrase of a tune that has vibrato. What remains in a phrase is state the
document cannot carry across phrases: the pulse binding after an instrument select that triggers
nothing (`rebinds`) and the sweep step after one (`stepped`), both of the instrument last
triggered.

`invariants.sizes` splits a document's bytes into the format's (header, tables, templates) and the
song's; `tests/lift_sizes_musicassembler.json` holds the song bytes of every committed fixture and
`invariants.grown` fails the test when one passes its baseline.
`python tools/lift_corpus.py NAMES.txt --format musicassembler [--all-subtunes]` prints per tune the
objects over native objects per kind, the song entries a voice over the orderlist's bound, the
fixed and song bytes and the native song data bytes.

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
`tempo` counter) at the `order_step` of the parse of the last row of the entry before it, so the
interval to the next row call is `t + 1`, and every later one, for every voice at once. The
document states it as the command `tempo:t+1` on the event of that row (the last row of the
entry's last repeat), a step beginning at that call taking the new length. Consecutive `$FC`
entries leave the last one's tempo. A phrase followed by a `$FC` is a phrase of its own (its
origin's context names the tempo); where every `$FC` keeps the tempo no command is stated.

The init does not pass a `$FC` at the start of a list (the voice's pattern cell holds `$FC`), and
the restart after the end marker reads its entry as a pattern without the tempo loop; both are
refused. The calls a silence under a stopping program counts (`gate_start`) depend on the tempo
in force, which other voices' lists change, so that combination is refused in a tune that
changes it.
