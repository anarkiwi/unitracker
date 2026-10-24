# defMON to the generic document, directly

`unitracker.direct.defmon` transcribes a defMON replay SID to the generic document (format 1.23)
from pydefmon's `DefmonPlayer`, which runs the tune's own replay on an emulated 6502. It reads
the replay's tables and sidTAB from memory after init and its pattern events from the emulated
calls, without the playroutine library or modfit. It is a standalone prototype: nothing else in
unitracker calls it.

```sh
python -m unitracker.direct.defmon TUNE.sid > tune.yaml
python -m unitracker.direct.defmon --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with
`DefmonPlayer(raw).render_grid` over `--frames` ticks; `--diff K` lists up to `K` differing runs
per register.

## Time

| defMON | generic |
|---|---|
| a play call's SID band writes the state the call before left | tick `t` is call `t - 1`'s pattern, sidcall, pitch and pulse work |
| the ACID step and cutoff write, before the call's other work | the `cut` slots, after the tick's programs: call `t`'s step |
| init's carried frequency and pulse cells (heard on call 0) | `init` and the lead-in phrase `p0: [-]` (tick 0) |
| a pattern event: a counter reaching bit 7 | an event on its call's tick; a step is one tick (`tempo: 1`) |
| the arranger, armed by init or an ALT event | the timeline: one row per song step, a phrase per voice |
| the arranger position and the voices' sidcall layers recurring | `{loop: k}` |

The pattern step is read off the emulated calls: before each call, the arranger flag `$10D9` and
each voice's counter and pattern pointer say what the call runs.

## Programs

An event arms sidcall layer A and layer B ($12DF), each a sidTAB row walked by count. Both run in
the control slot as one program: the lifter steps a voice's two cascades from the event (the other
layer as the event finds it), writes a row on each call that fetches (layer B's columns after
layer A's) with `hold` the calls to the next fetch, and ends where both are frozen or loops where
the cascades' state recurs. A note is a note event; the instrument `i1` binds only the pitch step.

| sidTAB column | row key |
|---|---|
| `wgh`, `wgl` | `ctrl: c^m`, `gate` (see Gate) |
| `ad`, `sr` | `ad`, `sr` |
| `tr` | `note`: `+tr`, or with bit 7 the absolute note `tr & $7F` |
| `af` | `p.freq`: `pitch()`, `slide(m)` ($80+), `por(m)` ($01-$7F) |
| `pw`, `ps` | `pw: pul(g, p, h)` |
| `re` | `res`, `route` (`'-'` for 0, `+ m` with bit 3) |
| `fv` | `mode` (the volume stays 15) |
| `cp`, `acid` | `kN.cut: acid(g, c, j)`, voice `N`'s ACID slot |

## Gate

The replay writes `wgh ^ wgl` to the control register. Where no row sets bit 0 of `wgl`, the gate
is on from init (`on`) and `ctrl` is `wgh`; where no row sets bit 0 of `wgh`, `ctrl` is `wgh | 1`
(`!ctrl:1` at init), the gate is `wgl`'s bit 0, and every note's first row must set `wgl`, since a
generic note turns the gate on.

## Modulators

Code templates (`unitracker.direct.dmcode`) over the replay's tables `G` (note frequencies), `P`
(portamento) and `R` (slide rates):

| cell | slots | template | replay |
|---|---|---|---|
| `freq` | `n` (reset on `note`), `p` | `nr(k, f)`: init's frequency and detune, a note clears the slide accumulator `s`; `pitch`, `slide`, `por` | the pitch step ($1405) |
| `pw` | `pw` (reset on `row.pw`) | `pinit(k, f)`, `pul(g, p, h)` over the 16-bit cell `w` and step `q` | the pulse step ($1495) |
| `cut` | `k1`-`k3` (reset on `row.cut`), `st` | `acid(g, c, j)` sets the accumulator, step or offset; `cinit(u, f, k)` steps it | the ACID step ($10B5) |

A row's `pw` and `cut` bindings reset on their row, so the same call re-applies its columns. Each
voice binds its own ACID slot, so two voices' rows on one call both apply.

## Not covered

The transcription raises `TranscribeError`, naming what it meets:

- a build other than the `$1000` build of the counted arranger with absolute notes, a play
  routine other than `$1003` (multispeed), a multi-SID tune;
- ACID opcodes that are not a matching SBC/ADC pair, a cutoff output that is neither NOP nor ASL,
  non-standard detune or routing constants, a slide accumulator init leaves set;
- a sidTAB row that resolves to a row with no body, both layers writing `$D417` or the ACID unit
  on one call;
- rows setting bit 0 of both `wgh` and `wgl`, a note whose first row does not set the gate where
  the gate is `wgl`'s, a note past `$7F`;
- a pulse sweep in a tune with a pitch mode (the sweep's borrow is the pitch step's carry);
- a song that does not loop within 131072 calls.
