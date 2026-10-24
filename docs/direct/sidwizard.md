# SID-Wizard to the generic document, directly

`unitracker.direct.sidwizard` transcribes a SID-Wizard SID to the generic document from
pysidwizard's playroutine (`pysidwizard.player.SWMPlayer`, a tick-for-tick transcription of
`player.asm`), without the playroutine library or modfit. It is a standalone prototype: nothing
else in unitracker calls it. It covers the bare and light builds (see Not transcribed).

```sh
python -m unitracker.direct.sidwizard TUNE.sid > tune.yaml
python -m unitracker.direct.sidwizard --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with the
reference player's (`SWMPlayer(parse_sid_song(raw, 1))`, its native note table) over `--frames`
ticks; `--diff K` lists
up to `K` differing runs per register.

## Time

| SID-Wizard | generic |
|---|---|
| a pattern row of `T` ticks (the subtune's single tempo) | two steps, `tempo: [2, T-2]` |
| TICK_0, TICK_1 (row read, hard restart) | the row's first step |
| TICK_2 (STRTSND and the row's columns) onward | the row's second step |
| the first play call | tick 0, TICK_0 of every voice's first row |
| the orderlists' pattern starts | the timeline: a row where some voice starts a pattern, `~` elsewhere |
| orderlist loop | `{loop: k}`, the earliest row from which the timeline repeats |

## A row's two events

| row | first step (TICK_0) | second step (TICK_2) |
|---|---|---|
| a note | `off!pw:hold!ad:15!sr:0!cut:hold`: HRGTOFF's gate-off and `$0F00` envelope; the pulse and filter tables do not walk on the HR ticks | the note, `.iN` where the row selects an instrument |
| a voice's first note | `B-1.i0`: the HR ticks walk the dummy instrument's header as WF rows | the note |
| `$3F` legato | `-` | the note `!legato!tick.freq:leg()` |
| `$60`-`$77` | `-` | `-!tick.freq:vibfx(k,f,p)` |
| `$7E` | `-` | `off` |
| instrument column `$5x`, `$6x` | | `!sr`: the sustain or release nibble over the instrument's other |
| empty | `-` | `-` |

A note whose STRTSND claims the filter in the same tick as a lower voice's carries `!filter`:
the player runs the voices last to first, so the lower voice holds the filter.

## Instruments

| SID-Wizard | generic instrument |
|---|---|
| AD, SR | `ad`, `sr` |
| WF table | `ctrl`: a first row `{ctrl: 9, route}` (STRTSND's control and SETFLTP's routing), then a row per tick: the waveform as `ctrl`, the arp as `note` (`+n`, `-n`, absolute) with `wave.freq: tone()`; `$80` (with WFARP NOP) no note; `$FE` a jump taken on its tick, `{loop: k}` back, `end` past `$7F`; `$FF` `end` |
| pulse table | `pw`: an idle first row, then a set row `{pw: w}`, a sweep `{hold: n, pw: v + d}` and its advance tick `{pw: none}`; a jump to a sweep spends a tick |
| filter table (claimed) | `filter`: a set row `{cut, res, mode}`, a sweep `{hold: n, cut: v + 8d}` (`v + d` with FINEFILTSWEEP) and its advance tick |
| vibrato, delay | `tick.freq: vib(d,k,f,p)` |
| the dummy instrument | `i0`: `ad: 15, sr: 0`, its header bytes walked as two WF rows from offset 0 |

## Frequency

The frequency cell has two code-modulator slots, `slots: {freq: {tick: {hold: [note-1, note-2]},
wave: {hold: [legato], reset: [row.note]}}}`, firing in the player's order: VIBSLIDE, then the
WF table's pitch and WRPITCH. The pitch is the standard note table `F`: generic note `k` (SID-Wizard
note `k + 1`) sounds `F(k)` at `clock_hz: 985256`, the clock whose table is nearest FREQTBL/FREQTBH.
No clock reproduces it: 19 of its 95 notes differ by one, so a document's frequency low bytes
differ from the native player's there, and `--verify` reports those cells.

| state | SID-Wizard |
|---|---|
| `o` | `vibrato_offset`, the offset the vibrato and slides add |
| `s` | the WF table has written the pitch since STRTSND (else the previous note's frequency holds) |
| `dc`, `vc`, `fm` | VIDELCNT, VIBRACNT, FREQMOD |

| template | routine |
|---|---|
| `tone()` | on `φ = 0` (a WF pitch row): `o = 0`, `s = 1`; with `s`, `v = F(N) + o` |
| `vib(d, k, f, p)` | on `φ = 0` STRTSND: `o = 0`, `s = 0`, the counters and amplitude; then NORMVIB and DOVIBRA |
| `vibfx(k, f, p)` | SETVIBR from the note's amplitude, `o = 0`, the delay left running, then the vibrato's step |
| `leg()` | on `φ = 0`: `o = v - F(n)`, FREQMODH `$7F`; then PORTAME toward the new note |

`f` is SETFMOD's amplitude of the vibrato nibble: these builds read it pitch-independent.

## Not transcribed

Refused with a `TranscribeError`:

- the normal, medium and extra builds: any of arp speeds, calculated or typed vibrato, chords,
  detune, hard-restart types, the first-frame switch, octave shifts, vibrato portamento, gate-off
  pointers, table reset switches, keyboard tracking, fast speed binding, all-ghost registers;
- multispeed, a play gate, an initial filter, a funk or 1-2 tick tempo, orderlist commands but
  patterns and a loop, and an orderlist that ends;
- the FX column; instrument-column small FX but `$5x` and `$6x`; note-column values `$78`-`$7D`;
  an instrument selected without a note; a note column before any instrument;
- a pulse or filter table that starts with a sweep (its count is left from before), a jump to a
  jump or an end, more than three chained WF jumps;
- a legato whose TICK_2 (which walks no table) the reference player reaches with a table still
  walking;
- a note of the voice holding the filter that does not take it (the filter would walk the new
  instrument's bytes from the old position).
