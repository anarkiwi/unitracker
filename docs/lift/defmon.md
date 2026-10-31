# defMON lift

`unitracker.lift.defmon` builds a format 2.5 document from pydefmon's song model (`Tune`, the
arranger, pattern events and sidTAB rows the replay reads) and the build facts of its bound
playspec (`build_facts`: the flags, the tables and the state the replay's init leaves). The
state init leaves is read from the spec player's memory before its first call, and the schedule
of full and sub-tick calls from the entries the tune's own interrupt enters in its first calls;
nothing else of a player is run or read to build the document.

```sh
python -m unitracker.lift.defmon TUNE.sid > tune.yaml
python -m unitracker.lift.defmon --verify [--frames N] [--diff RUNS] [--jobs J] TUNE.sid ...
python tools/lift_corpus.py NAMES.txt --format defmon
python tools/dmtrace.py HVSC/NAME.sid FRAMES CELL[@VOICE]...
```

## Time

Call `c` of the document is call `c` of the replay, and `lag:1` writes each frame from the call
before, as the replay's SID band does. The tune's schedule of full and sub-tick calls is
`calls:{frame:1,full:[...]}`. Cells that the replay computes before its band writes (the cutoff)
take their first step from the cleared state in `regs`.

| defMON | document |
|---|---|
| a step reader's counter reaching the next step, `d + 2` full calls | a phrase tick (`tempo:1`) |
| the walker loading three patterns, the previous ALT step's delay `t` | a phrase of `t + A + 2` ticks, `A` the first ALT step; events at `t + 1 + at` |
| the last voice's ALT step setting the delay | the delay of the last voice that ends at `A` |
| arranger row, counted jump with its shared owner and count | the walk, unrolled until a state (row, owner, count, delay) recurs: `voice:[...\|loop:k]` |
| init's `trigger`, `cursor`, `owner`, `count` | the walk's start state |

## Cells

| defMON | document |
|---|---|
| pattern event gate `a`, `b` (row to arm) | `run:cN`, `b.run:cN`: layers `ctrl:[a,b]`, A over all voices then B, as the six cascade blocks |
| pattern event note `k` | the note `k - 24`, an instrument `iN` per voice |
| sidTAB row, `DL`, JP rows | a `ctrl` program row with `hold`, `end` at a stop row, `{loop:k}` at a revisited row |
| `AD`, `SR` | `ad`, `sr` |
| `WGh`, `WGl` | `ctrl` (one column), else slots `h` (a constant binding) and `l` (`v ^ c`, `v \| c` in the OR build) |
| `TR` | `note:+t` (relative build), else `note:+t` or the absolute note `(t & 127) - 24` |
| `AF` mode 0, $01-$7F | `p.freq` expression: the note word and the detune, or the portamento step |
| `AF` mode $80+ | `p.freq` template `up`/`dn` of the slide rate; the accumulator `s` clears on a note (`n.freq`) |
| `PW`, `PS` | `pw` (no sweep), else once bindings of the pulse state `l`, `h`, `d` and the sweep `psw` |
| the sweep's carry from the pitch step | slot `m` of `pw`: an expression of the note, or a mirror template `cup`/`cdn` of the slide |
| `RE` | `res` and a once binding of the voice's `route` slot (`v & ~bit`, `v \| m \| bit`, constant) |
| `FV` | `mode` (the volume is always 15) |
| `ACID` | once bindings `acc`, `add`, `sub` of the voice's `cut` slot; the step `step`/`stepx` |
| `CP` | `cp`, the cutoff's extra, where the player's CP store is that operand |
| note word | the standard table: index `k` is note `k - 24`, `notes(lo\|hi,-24)` |

Rows of the same tick apply in the order of the replay's blocks because the routing, the cutoff
and the pulse writes are once bindings of slots in that order. An instrument's `pw` field
applies in the pulse phase, so `order` runs it before `ctrl`.

## Refusals

Typed `Refusal(kind)`, raised before the document is built: `format` (not a defMON replay),
`multi-SID` (stereo: `defmon.play` is the single-SID routine), `digi` (a play source other than
the video or CIA 1 timer), `native` (a spec with native blocks), `jp onto jp` (a row read from
the cells the row interpreter writes), `no ending pattern` (a stream with no ALT step in the
64 KiB after its pointer), `multispeed` (a schedule that is not periodic), `init` (state init
leaves that the document cannot start from), `note` (a note past the absolute build's 7-bit wrap),
`arranger` (a walk with no loop).

The replay reads what its memory holds, and the lift reads the same bytes. A pattern is the
stream at the pointer of its table entry, step after step to its first ALT step, so a pattern of
more than 32 steps continues into whatever follows it; an arranger byte of $80 or more indexes the
pointer tables past their end; a JP row whose target is a JP row (`rows_hi` 0 twice) is read from
page zero, an empty row in the snapshot init leaves. None of these is a limit of the format.

## Size

`native_counts` is the most of each kind a document may hold, from the song data the replay can
reach: instruments 3, one a voice; ctrl programs one a `(voice, layer, row)` the played events arm
and one a voice for the init event's first-tick program; restart, pw and filter programs 0; phrases
one a `(voice, pattern, ALT step, delay after)` of a played arranger row, times the full calls in a
period where the document has a full call of its own, and three for the first step; song entries
one an arranger row reached, a jump row once. `verify` and `lift` check the document against it
(`unitracker.lift.invariants`); `tools/lift_corpus.py` prints each tune's counts of both and its
document bytes against the song data's.

## Verification

`verify` plays the document with unitracker-player and compares its register grid cell by cell
with `fmt.spec_player(song, notes=True).render_grid`: the replay's play routine spec with every
read of the note table answered with the standard word at the tune's clock. `lift` raises
`Divergence` naming the first differing frame and register.
