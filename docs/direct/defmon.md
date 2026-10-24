# defMON to the generic document, directly

`unitracker.direct.defmon` transcribes a defMON replay SID to the generic document (format 2.3)
from pydefmon's `DefmonPlayer`, which runs the tune's own replay on an emulated 6502. It reads
the replay's tables and sidTAB from memory after init and its pattern events from the emulated
calls, without the playroutine library or modfit. It is a standalone prototype: nothing else in
unitracker calls it.

```sh
python -m unitracker.direct.defmon TUNE.sid > tune.yaml
python -m unitracker.direct.defmon --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with
`standard(raw).render_grid` (see Pitch) over `--frames` ticks; `--diff K` lists up to `K` differing runs
per register.

## Builds

The replay is read at `dmimage.locate`'s places of the `$1000` build's addresses, so a relocated
build reads as the `$1000` one. Its variants: the plain arranger has no jump counts (its position
key holds 0 for them); a build of relative notes adds `tr` to the note base unwrapped, so the
templates read the note without the 7-bit wrap of the absolute build, and `tr` is always a relative
`note`.

A multispeed play routine calls the replay's play entry or its sub-tick entry, which skips the
arranger and the pattern step. A call is full where it steps voice 1's counter or arms the
arranger; only full calls run events. Each song step's key holds which of the last 64 calls were
full (the play routine's phase), and the lead-in covers the sub-tick calls before the first full
call.

## Time

| defMON | generic |
|---|---|
| a play call's SID band writes the state the call before left | tick `t` is call `t - 1`'s pattern, sidcall, pitch and pulse work |
| the ACID step and cutoff write, before the call's other work | the `cut` slots, after the tick's programs: call `t`'s step |
| init's carried frequency, pulse cell, slide accumulator and note base | `init` and the lead-in phrase `p0` |
| a pattern event: a counter reaching bit 7 on a full call | an event on its call's tick; a step is one tick (`tempo:1`) |
| the arranger, armed by init or an ALT event | the timeline: one row per song step, a phrase per voice |
| the arranger position, the voices' sidcall layers and gate bits recurring | `{loop:k}` |

The pattern step is read off the emulated calls: before each call, the arranger flag `$10D9` and
each voice's counter and pattern pointer say what the call runs. The init's note base `$12ED` is
a legato init note; init's cleared note 0 is the absolute note of the one-row program `c0`, which
the first relative row or note replaces.

## Pitch

defMON note index `k` is generic note `k - 24` (index `$30` is `C2`, indexes 0-23 `C-2` to
`B-1`), and its word is the standard `F(k - 24)` at the document's `clock_hz`: the templates read
it as `F.lo-24,y` / `F.hi-24,y` with the index in Y, and the portamento table `P` by the index
itself. The replay's note table is the standard `F` at PAL's clock two octaves up from index 24
to 118; its own words under those (two octaves below C0) and over them (its top clamped word,
then the memory a wrapped or relative note reaches, to index 127 in the absolute build and 255 in
the relative one) are not carried: there the document plays `F`, 0 where the word overflows.

Documents are verified against `standard()`, the replay with every note-table read answered by
`F(k - 24)` at the document's clock (`compare(..., native=True)` against the replay's own table).
Against the native replay, tunes that play notes under index 24, past 118, or at NTSC differ in
those notes' frequencies only.

## Programs

An event arms sidcall layer A and layer B ($12DF), each a sidTAB row walked by count. Both run in
the control slot as one program: the lifter steps a voice's two cascades from the event (the other
layer as the event finds it), writes a row on each call that fetches (layer B's columns replacing
layer A's) with `hold` the calls to the next fetch, and ends where both are frozen or loops where
the cascades' state recurs. A note is a note event; the instrument `i1` binds only the pitch step.

| sidTAB column | row key |
|---|---|
| `wgh`, `wgl` | `ctrl:c`, `mask:m`, `gate` (see Gate) |
| `ad`, `sr` | `ad`, `sr` |
| `tr` | `note`: `+tr`, or with bit 7 in the absolute build the absolute note `tr & $7F` |
| `af` | `p.freq`: `pitch()`, `pslide(m)` ($80+), `por(m)` ($01-$7F); `pm.pw:pmode(m)` |
| `pw`, `ps` | `sw.pw:pul(g,p,h)` |
| `re`, `fv` | layer A's: `res`, `route` (`route_off:0` for 0, `route_on:m` with bit 3), `mode`; layer B's: `run:` a one-row pulse-slot program of them |
| `cp`, `acid` | `kNa.cut`, `kNb.cut:acid(g,c,j,k)`, voice `N`'s layer A and B ACID slots |

The replay applies every voice's layer A, then every voice's layer B: layer B's ACID slots fire
after every voice's layer A slots, and its `$D417`/`$D418` columns run in the pulse slot, whose
phase follows every voice's control program.

## Gate

The replay writes `wgh ^ wgl` to the control register: `ctrl` is `wgh | 1` (`ctrl:1` at init),
the mask `wgl & $FE`, and a row writing either sets `gate` to bit 0 of `wgh ^ wgl`, tracked
through each program from the bits at its start (part of the program's key). A note turns the
gate on; where its call leaves the replay's gate off, the note runs the voice's program from its
state with the gate off on that call.

## Modulators

Code templates (`unitracker.direct.dmcode`) over the standard note table `F` and the replay's
tables `P` (portamento) and `R` (slide rates):

| cell | slots | template | replay |
|---|---|---|---|
| `freq` | `n` (reset on `note`), `p` | `ninit(k,u,f)`: init's detune, slide accumulator and frequency; `nr()`: a note clears the slide accumulator `s`; `pitch`, `slide`, `por` | the pitch step ($1405) |
| `pw` | `pz` (reset on `note`), `pm` (reset on `row.freq`), `sw` (reset on `row.pw`) | `pzero`, `pmode(m)`: the pitch mode and slide accumulator mirrored for the pulse step's carry; `pinit(k,f,u)`, `pul(g,p,h)` over the 16-bit cell `w` and step `q` | the pulse step ($1495) |
| `cut` | `k1a`-`k3a`, `k1b`-`k3b` (reset on `row.cut`), `st` | `acid(g,c,j,k)` sets the offset, accumulator or step; `cinit(u,f,k)` steps it | the ACID step ($10B5) |

A row's `pw` and `cut` bindings reset on their row, so the same call re-applies its columns; a
row binding any `pw` or `cut` slot binds each of its voice's (a call of no columns keeps the
state), so the reset leaves the others' columns unapplied.

## Not covered

The transcription raises `TranscribeError`, naming what it meets:

- a replay whose code `dmimage.locate` does not find or places twice, a multi-SID tune;
- ACID opcodes that are not a matching SBC/ADC pair, a cutoff output that is neither NOP nor ASL,
  non-standard detune or routing constants;
- a sidTAB row that resolves to a row with no body;
- a note past `$7F` in the absolute build;
- a song step that reaches no ALT event, a song that does not loop within 2^20 calls.
