# defMON lift

`unitracker.lift.defmon` builds a format 2.5 document from pydefmon's model of a replay
(`Tune.from_replay`: the arranger rows, pattern events and sidTAB rows its play routine reads, and
`Tune.init`, the state its init leaves). The schedule of full and sub-tick calls comes from the
entries the tune's own interrupt enters in its first calls; nothing else of a player is run or
read to build the document. What the model does not hold is read from the replay's memory at the
addresses `build_facts` names: the routing bits and the cutoff's patched operands, the slide rate
table, and the pattern and row pointers the song data byte count parses.

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
| arranger row, counted jump with its shared owner and count (`Tune.chips`, `Tune.landed`) | the walk, unrolled until a state (row, owner, count, delay) recurs: `voice:[...\|loop:k]` |
| `Tune.init`'s `trigger`, `cursor`, `owner`, `count` | the walk's start state |

## Cells

| defMON | document |
|---|---|
| pattern event gate `a`, `b` (row to arm) | `run:cN`, `b.run:cN`: layers `ctrl:[a,b]`, A over all voices then B, as the six cascade blocks |
| pattern event note `k` | the note `k - 24`, an instrument `iN` per voice |
| sidTAB row, `DL`, JP rows | a `ctrl` program row with `hold`, `end` at a stop row, `{loop:k}` at a revisited row |
| `AD`, `SR` | `ad`, `sr` |
| `WGh`, `WGl` | `ctrl` (one column), else slots `h` (a constant binding) and `l` (`v ^ c`, `v \| c` in the OR build) |
| `TR` | `note:+t` (relative build), else `note:+t` or the absolute note `(t & 127) - 24` |
| `AF` mode 0, $01-$7F | `p.freq`: the note word, with the detune as `det` (state `g`), or the portamento step |
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

Typed `Refusal(kind)`, raised before the document is built: `format` (not a defMON replay, or no
player in its memory), `multi-SID` (stereo: `defmon.play` is the single-SID routine), `digi` (a
play source other than the video or CIA 1 timer), `native` (a spec with native blocks), `jp onto
jp` (a page-zero row read from the cells the row interpreter writes), `no ending pattern` (a stream
with no ALT step in the 64 KiB after its pointer), `multispeed` (a schedule that is not periodic),
`init` (state init leaves that the document cannot start from), `note` (a note past the absolute
build's 7-bit wrap), `arranger` (a walk with no loop). pydefmon's `DefmonError` is mapped to the
first three by its message.

The model reads what the replay's memory holds: a pattern is the stream at the pointer of its table
entry, step after step to its first ALT step, so a pattern of more than 32 steps continues into
whatever follows it; an arranger byte of $80 or more indexes the pointer tables past their end; a
JP row whose target is a JP row is read from page zero. None of these is a limit of the format. The
tunes that enter the arranger from a reduced entry (sub-tick calls first) are read with
`Tune.from_replay(memory, entry=...)` at the first full entry the schedule enters.

## Provenance and size

Every named object of the document stands for one native object (`invariants.Origin(native,
context)`), checked by `invariants.provenance` in `verify` and `lift`: none without an origin, no
two of one origin, none repeating another's value, none written without a name.

| object | native | context |
|---|---|---|
| instrument | the voice (`("voice", v)`): the voices' instruments are one value | none |
| `ctrl` program | the sidTAB row it is armed at (`("row", y)`) | the `("voice", v)` and `("layer", l)` its slots name |
| init program | the voice's first-tick program (`("init", v, "a")`) | none |
| phrase | the pattern (`("pattern", p)`) | `("last", n)`, `("after", n)`: the pattern is cut at the earliest ALT step of its row and padded by the delay that step sets; `("voice", v)` where its programs name the voice's slots; `("delay", n)` for the first step; `("phase", n)` in a schedule with an extra full call |

A voice's detune is a state variable of the pitch cell (`g`) and of the pulse cell (`k`), set by the
init event, so the pitch, the carry and the init program do not name the voice. A program names the
voice and layer where it binds a route, cutoff or CP slot: those cells fire their slots in the
replay's order (layer, then voice), which the slot names carry. `check` bounds each voice's
orderlist entries by the arranger's steps (a jump row once, a counted jump folded into `xN`).

`invariants.sizes` splits a document's bytes into the format's (`fixed`) and the song's. The song
bytes of every committed fixture are kept in `tests/lift_sizes_defmon.json`, and a lift that
grows one fails `tests/test_lift_sizes_defmon.py`. `tools/lift_corpus.py` prints per tune the
objects and native objects of each kind, the fixed and song bytes and the native song data bytes (the
arranger's rows, the pattern streams to their ALT steps and the sidTAB rows with their DL, as the
byte grammar parses them), then the corpus totals and copies; `tools/lift_origins.py TUNE` lists
the native objects with more than one object and the contexts that tell the copies apart.

## Verification

`verify` plays the document with unitracker-player and compares its register grid cell by cell
with `fmt.spec_player(song, notes=True).render_grid`: the replay's play routine spec with every
read of the note table answered with the standard word at the tune's clock. It plays at least 20000
frames and every step of the walk and the loop once more (`whole=False` plays the frames asked
for). `lift` raises `Divergence` naming the first differing frame and register.
