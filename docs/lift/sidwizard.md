# SID-Wizard lift

`unitracker.lift.sidwizard` builds a format 2.5 document from pysidwizard's typed module
(`SWMFile` of a `.sid`: orderlists, patterns, instruments with their tables, chords) and the
layout of the bound playroutine spec (`pysidwizard.playspec.binding`: the build's flags and
addresses), with the memory the tune's init left for the tempo program and the vibrato tables.
`unitracker.lift.swcode` holds the 6502 modulators.

```sh
python -m unitracker.lift.sidwizard TUNE.sid > tune.yaml
python -m unitracker.lift.sidwizard --verify [--frames N] [--jobs J] TUNE.sid ...
python tools/lift_corpus.py NAMES.txt --format sidwizard
python tools/swtrace.py TUNE FRAMES CELL[@VOICE]... [--regs VOICE]
```

## Time

A row begins on call `c` (tick 0 of the player's clock) and its note starts on call `c + 2`. The
document's step is the span from the note's call: `init` states `tempo:2`, the intro phrase `[-]`
is the two calls before row 0, and the first event of voice 1 carries the tune's `tempo`. The
rows' length is the tempo program's: one or two alternating lengths (`tempo:a/b`), and the
`$10` and `$11` effects are `tempo:n` and `tempo:a/b`.

| SID-Wizard | document |
|---|---|
| tick 0 and 1 of a row with a note | the `restart` program's first two rows: the hard restart (`ctrl` bit 1 on tick 0, bit 0 on tick 1) |
| tick 2, the note starts | the last two rows of `restart` (the first, `hold:0`, lets a note's `ad`, `sr` and `ctrl` commands apply with it): `ad`, `sr`, the first waveform, the filter switch |
| later ticks | the programs (`ctrl`, `pw`, `filter`) and the `freq` slots; on the note's calls `note-2`, `note-1` and `note` the `pw`, `filter` and `tick` slots hold, as the player does not run those tables there |
| a note with a tone portamento, legato column or a portamento note effect pending | a `legato` event (no restart) |

## Cells

| SID-Wizard | document |
|---|---|
| waveform table, `ARPSPEED` | `ctrl` program, a row `hold:S+1`; repeat rows `hold:n+1`; a jump follows to the target and onto a repeat row writes its count as the waveform; the stopped table is a row repeated every step |
| waveform written `wave & PTNGATE` | state `pg` of `ctrl` (`wv`, `goff`, `gon`, `arm`); the key row writes `wf.ctrl` after the effects' `ctrl` slot |
| pitch column | `note:+r` or an absolute note, loaded to `freq` by `ld`/`ldr`/`kp`; the carry it leaves is added to the next register write (`out`) and taken off before the next call's slots (`un`) |
| chord row | one row for each interval of the chord (`chord(n)`), a looping chord loops the program |
| instrument vibrato, slides, tone portamento, `VIBSLIDE` | the `tick` slot, state `ml mh vd vf vk sv`; `vset`, `vibr`, `sld`, `tone`, `leg` load it |
| the calculated vibrato and slide step | table `V` (the player's `EXPTBASE`, `VIB_LO`, `VIB_HI`) indexed by the note |
| pulse table | `pw` program: a set row `pw:n`; a sweep `{hold:frames,pw:'v + step'}` then `{pw:none}` (the call that advances); a jump onto a sweep costs a call |
| pulse keyboard track | slots `un`, `walk`, `out` of `pw`, state `pk`, `pr`; tables `E`, `M` (`EXPTABH`) |
| filter table | `filter` program on `cut`, `res`, `mode`, with the same sweep and jump rows; a column `$80`-`$8F` is a `route` write |
| filter switch | `route_on:0` or `route_off:0` in the note row; an empty filter table also stops the voice's own program (`cut:hold`) where the build rewinds it |
| first frame | `wf.ctrl` the first waveform, the high byte of the note's frequency in the `first` slot (`fix`, or `fixb` and the voice's `seed` where the build reads it by voice) |
| gate off, gate on | `ctrl` commands `goff`, `gon`; the instrument's gate-off table positions run the waveform and pulse tables from there |
| `$01`-`$03` | `sld(0x81..0x83, q)`; `$08` and the vibrato note effect `$60`-`$6F`: `vibr`; `$05`, `$06` and the small effects `2 3 5 6`: `ad`, `sr`; `$09`, `$0A`, `$0B`: the table runs from the row |
| sync and ring note effects | `ctrl` commands (`v \| 2`, `v & 253`, ...) |
| a voice's first note | the player runs its empty instrument on the two restart calls; the copy of the instrument that note starts writes what the spec player writes there (`boot.freq`, `pw`, `wf.ctrl`) |

The orderlist is `voice:[...]`, a transpose the entry's `+t`, `$FE` the end and a loop `loop:k`;
the loop body is written again where entering it in another context changes a phrase.

## Refusals

Typed `Refusal(kind)`, raised before the document is built; `detail` names the construct.

| kind | the construct |
|---|---|
| `multi-SID` | a second or third SID |
| `interrupt` | play address 0 |
| `partly relocated` | a player relocated in part (the spec does not state it) |
| `player` | no modelled play entry |
| `build` | a release or driver the lift does not state: ghost registers, multispeed, SFX, SWP exports, `portavibra`, fast speed, ... |
| `tempo program` | row lengths other than one or two alternating, or voices on different tempos |
| `orderlist` | a volume or tempo entry, a subtune jump, an effect before a loop, a pattern the module lacks |
| `instrument`, `note without instrument`, `instrument change` | an instrument the module lacks, a note before any, an instrument selected without a note start |
| `table jump`, `table position` | a jump onto a jump row or between rows; a table position set between the steps of an arpeggio |
| `pulse sweep` | a pulse table that starts on a sweep: the player does not reset its count, so the first sweep runs on what the last table left |
| `filter keytrack`, `filter sweep`, `filter without control` | a filter keyboard track; a filter table starting on a sweep; a table that takes no control, which leaves the running program reading this instrument's bytes |
| `detune`, `chord` | a detune other than 0; a chord the module lacks |
| `pattern effect`, `note column`, `gate off pointer` | an effect, a note effect or a gate off position of the filter table not stated |
| `start state` | a first note that gates a voice on in its restart |

## Reference

The reference is the spec player of pysidwizard (`find_format(raw).spec_player(tune,
notes=True)`) with the standard note table. `verify` plays the document with unitracker-player and
compares the register grid and the gate and TEST edges within each frame
([verification](verification.md)); `lift` raises `Divergence` naming the first differing frame and
register. The checked span is at least the frames asked for and the song with its loop once more.

SID-Wizard calls the voices last first and the document calls them first first, so when two voices
start a filter program on one call the player keeps the first voice's and the document the last's:
the lift diverges there rather than state it (Friendly Fire).

## Objects and sizes

Every named object of a document has the native object it transliterates
(`invariants.provenance`): an instrument and its restart, a table program with its entry row, a
pattern with the context it is entered in. A voice's first note starts a copy of its instrument
(`("ins", n, ("first", voice, call))`) and the next event restores the instrument. Orderlist
entries are bounded per voice by the entries of the orderlist and its loop body once more.
`tests/lift_sizes_sidwizard.json` holds each exact fixture's song bytes; `grown` must be empty.
