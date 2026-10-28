# SID-Wizard to the generic document, transliterated

`unitracker.direct.swlift` writes a SID-Wizard SID's song data, as pysidwizard's `SWMFile`
models it, as a format 2.4 document: each native object is one document object, and the player
runs only to verify.

```sh
python -m unitracker.direct.swlift TUNE.sid > tune.ut
python -m unitracker.direct.swlift --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
python tools/swdiff.py TUNE.sid [--frames N] [--regs v1_ctrl ...] [--why]
python tools/swsample.py out/learn/tunes-ma.csv --hvsc $HVSC [--count 200] [--seed 0]
```

The semantics are SID-Wizard 1.9's user manual (section III) and `player.asm` as pysidwizard's
player cites it.

## Objects

| SID-Wizard | document |
|---|---|
| instrument | `iN`: AD, SR, its WF, pulse and filter walks, STRTSND (`snd.freq`), its default chord (`ch.freq`), VIBSLIDE (`tick.freq`), its `restart` |
| hard restart (timer bits 0-1, staccato bit 2, ADHR) and frame-1 waveform (bit 3) | a `restart` template per shape: TICK_0's and TICK_1's rows (`gate:0`, ADHR, `$18` staccato), a `hold:0` row, the note's row (the frame-1 waveform, SETFLTP's `route_on`/`route_off`) |
| WF/arp/detune table from an entry point (start, gate-off pointer, `$09` row) | a `ctrl` program template `wfN(l)` on layer `wf`: each read a row (`ctrl`, `note`, the `wave.freq` pitch code), a repeat row `hold:n+1`, the table speed the hold, `$FE` the loop |
| pulse table from an entry point (start, gate-off pointer, `$0A` row) | a `pw` program template `ptN(l)`: `$8x` `pw:w`, `n` adds `{hold:n,pw:v + d}` then a tick, `$FE` the loop |
| filter table from an entry point (start, `$0B` row) | a `filter` program template `ftN(l)`: `$8x` `cut`, `res`, `mode`, sweeps as the pulse's |
| keyboard tracking (a third column anywhere in the module's tables) | the walk's rows bind swcode's tracked pulse or cutoff code (`pst`, `psw`, `pid`, `pwr`; `fst`, `fsw`, `fid`, `frs`) |
| chord table | the table `C`, which the WF rows' pitch code walks from `cs`/`cp` |
| pattern | `pN`: a row a step; notes are CLEGATO's DPITCH less one (the instrument's octave shift in them) |
| orderlist | a voice's orderlist: its transposes on the play entries, `$FF` the loop, `$FE` the end |
| subtune tempo, `$10`, `$11`, `$13`, `$14` | `tempo`, `tempo:a/b`, `vtempo` |
| note-off `--` / gate-on `++` | `g.run:c1` / `g.run:c2`, the gate on layer `g` (the gate-off pointers start their walks instead) |
| `$3F` legato, `$03` on a note, a note after `$78` | `legato` and the glide's setup (`fx.freq`) |
| `$01`-`$03`, `$08`, `$16`, `$6x` note, `$8x`, `$9x` | the VIBSLIDE setup, once, on `fx.freq` |
| `$04`, `$05`, `$06`, `$1F`, `$Ax`, `$Bx`, `$Fx`, `$2x`/`$3x`/`$5x`/`$6x` | `ctrl`, `ad`, `sr`, `res` and `route`, `vol`, `mode`, `res`, the merged ADSR byte |
| `$0D`, `$Dx` | DETUNER, once, on `dset.freq` |
| `$07`, `$7x` | the chord, once, on `ch.freq` |

The `l` of a walk's program is the ticks to its first read: 1 from a note (STRTSND's tick walks
nothing), 0 from a gate-off pointer or a position effect on any other row.

## Time

A step is a row from its TICK_2: the row's read (TICK_0) and hard restart (TICK_1) are its
note's restart's lookahead. The document starts at the first row's TICK_2; its `regs` are the
registers the reference holds there, and `--verify` compares its grid with the reference's from
that tick. The tick slot, the pulse and the cutoff hold on the note's tick and on legato, and on
the two ticks before a note where HARDRST skips them (without VIBSLIDEALWAYS).

## Gaps

What the grammar cannot say one-to-one, each recorded by the lift as a gap at its site:

| SID-Wizard | why |
|---|---|
| the first row's TICK_0 and TICK_1 | the song starts on its first step: there is no tick before it, so the document starts at the first TICK_2 |
| a walk pausing on a note's TICK_2 (legato, portamento, a note keeping its pulse or filter walk) and on HARDRST's ticks | a program's rows hold for fixed ticks; nothing pauses a program |
| a chord ending `$7E`, which moves the WF walk on | a program row holds for fixed ticks, so the row holds the default chord's reads; another chord the effects select ends elsewhere |
| an instrument without the frame-1 waveform | STRTSND keeps the hard restart's byte (its gate bit clear) on the note's tick; the note sets the gate on with the cell's byte |
| a gate-off pointer or position effect at a table speed | its first read waits for ARPSCNT, a phase of the run |
| a voice holding the filter playing an instrument without a filter program | FILTPRG reads the new instrument's bytes from the old offset |
| two voices claiming the filter on one tick | the player runs voices last to first, the grammar first to last |
| an instrument selected without a note | the walks go on through the new instrument's bytes; before a voice's first note, its header |
| a voice's first hard restart after its first row | HRENDER walks instrument 0's WF table |
| `$E0`-`$EF`, sync and ring `$79`-`$7C` | OR or AND into the control byte, which the grammar assigns whole |
| `$0E`, `$0F`, `$0C`, `$12`, `$15`, `$17`-`$1C`, `$1D`, `$1E` | a nibble of a cell, the table speed, tempo programs, filter shift, delays |
| an orderlist tempo, volume or subtune jump; a loop into another transpose | a play entry carries only a transpose |
| a pattern played from voice states that change its events (an instrument's octave, NPORTAM before it) | one phrase per pattern |
| multispeed, more than one SID | refused |
