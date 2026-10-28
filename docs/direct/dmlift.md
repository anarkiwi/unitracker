# defMON to the generic document, by transliteration

`unitracker.direct.dmlift` writes a defMON replay SID as a format 2.4 document by reading the
tune's own data from the replay's memory after init: the arranger, the pattern streams and the
sidTAB rows with their JP and DL bytes, at `dmimage.locate`'s places of the `$1000` build's
addresses. Each native object becomes one document object, so the document grows linearly with
the tune's data. It is the `defmon` lifter of `unitracker-lift`; the replay runs only to verify,
and once at lift time to find the play routine's cadence (below).

```sh
python -m unitracker.direct.dmlift TUNE.sid > tune.yaml
python -m unitracker.direct.dmlift --verify [--frames N] [--jobs J] TUNE.sid ...
python tools/dmgaps.py TUNE.sid ...                     # differing control runs by cause
python tools/dmgaps.py --corpus LIST.csv [--hvsc DIR]   # every DefMon tune of a tune list
```

`--verify` compares the document's grid with `pydefmon.DefmonPlayer`'s over the song's length
(init, the lead-in and every arranger item once, at most `MAX_TICKS`).

## Objects

| defMON | document |
|---|---|
| a pattern | a phrase `pN`, one event per pattern event |
| a pattern event (flag, A, B, note) | a note (`GATE_N`) or `-`; `run:cA` (layer `a`, `GATE_A`) and `b.run:cB` (layer `b`, `GATE_B`); `*k` its duration nibble plus 2 |
| an arranger step | an entry of each voice's orderlist (`- voice:[...]`) |
| an unconditional arranger jump | the orderlists' `loop:k` |
| a counted arranger jump | a group `[...]xN`, its body played count + 1 times |
| a sidTAB entry a pattern event arms | a control program `cN` |
| a sidTAB row | a program row: its columns' keys, `hold:` DL + 1, `end` after a STop row |
| a JP row | the walk continues at its target; `{loop:k}` where the target is a row of the program |
| sidcall layers A and B | `layers:{ctrl:[a,b]}`: the replay runs every voice's layer A, then every voice's layer B, as the player runs layers |

The lead-in phrase `p256` holds init's tick, the play routine's calls before its first full one,
which arms the arranger (`$10D9`), and the steps to the first pattern events; it binds the pitch
step and sets the tempo. Where init leaves a voice's note (`$137F`) apart from its note base
(`$12ED`, the note TR adds to), init's note takes an instrument whose one-row program `c512+n`
sets that note `n`.

## Time

A tick is a call of the play routine. A step is the calls from one full call (one that runs the
arranger and the pattern step) to the next, read off the play routine at lift time (`cadence`);
sub-tick calls skip the arranger and the pattern step. Tick `t` is call `t - 1`'s pattern,
sidcall, pitch and pulse work, which call `t`'s SID band writes, and call `t`'s ACID step.

A pattern event holds its duration nibble `d` and waits `d + 2` steps ($1128: the counter counts
down past 0, then the next call prefetches). The first ALT any voice plays arms the arranger,
which restarts every voice on the next full call with that ALT's duration (voice 3's over voice
1's), so a step ends at its earliest ALT and a pattern that runs past it is cut there.

## Rows

| sidTAB column | row key |
|---|---|
| `WGh`, `WGl` | `ctrl:` the byte the band writes (`$103B EOR $103D`, or `ORA`), see Control |
| `AD`, `SR` | `ad`, `sr` |
| `TR` | `note:+tr`; in the absolute build with bit 7 the absolute note `tr & $7F` |
| `AF` | `p.freq:` `pitch()`, `pslide(m)` (`$80+`) or `por(m)`; `pm.pw:pmode(m)` |
| `PW`, `PS` | `pa.pw:pwset(p)!`, `pb.pw:psset(h)!` |
| `RE`, `FV` | `res`, `route` (`route_off:0` for 0, `route_on` with bit 3), `mode` |
| `CP` | `kc.cut:cpset(c)!` |
| `ACID` | below `$8000`: `kr.cut:accset(j)!`, `kz.cut:stepset(0)!`; else `kz.cut:stepset(k)!`, `ko.cut:dirset(u)!` |

Each cell a column assigns is a slot of its own bound once (`!`), so when rows of several voices
and layers set it in one tick the last binding wins, as the last write does in the replay. The
steps that run every call are slot defaults: `n:nr()` (a note clears the slide accumulator),
`pz:pzero()`, `sw:pul(0,0,0)` (the pulse sweep) and `st:cinit(u,f,k)` (the ACID step); init
binds `ni:ninit(...)!` and `pi:pinit(...)!` with the cells init leaves.

The templates are `unitracker.direct.dmcode`'s, reading the replay's own note table, carried as
the table `W` (defMON note index `k` is `W[k]`, generic note `k - 24`), with its portamento
table `P` and slide rates `R`.

## Control

A row's `WGh` and `WGl` each write one nibble of the control byte; an absent one (`-` in the
editor) leaves the replay's shadow of that nibble. A row's byte is resolved from the rows of its
own program before it on every pass of the program's walk. The grammar's `ctrl:` writes the
whole byte, so where that byte depends on what ran before the program (another program on the
voice, the other layer) or resolves two ways around the program's loop, the row writes no
`ctrl:` and `Lift.gaps` lists it as `(entry, row, nibble pairs)`.

## What the grammar cannot spell

- A nibble of the control byte (Control): `Lift.gaps`; `tools/dmgaps.py` names the row behind
  each differing control run, a `gap` row or a `shadow` row (resolved in its program, but the
  other layer wrote the nibble between).
- A phrase cut short by another voice's ALT: a pattern a step cuts at another length is a
  phrase of its own (`Lift.cuts`).
- A play routine whose full calls are not evenly spaced: refused.

## Not covered

`LiftError` names what it meets: a replay `dmimage.locate` does not find or places twice; a
multi-SID tune; non-standard detune or routing; ACID opcodes that are not a matching pair, a
cutoff output neither NOP nor ASL; an uneven play routine; init leaving the arranger unarmed;
patterns of a step none of which reaches an ALT; a jump onto a jump row (sidTAB or arranger, which
the replay reads as data); a counted arranger jump forward past the next step.
