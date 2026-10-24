# SID-Wizard to the generic document, directly

`unitracker.direct.sidwizard` transcribes a SID-Wizard SID (every build: bare, light, normal,
medium, extra) to the generic document from pysidwizard's playroutine
(`pysidwizard.player.SWMPlayer`, a tick-for-tick transcription of `player.asm`), without the
playroutine library or modfit.

```sh
python -m unitracker.direct.sidwizard TUNE.sid > tune.yaml
python -m unitracker.direct.sidwizard --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with the
reference player's (`swtrace.standard(swm)`: `SWMPlayer` at the document's note table) over
`--frames` ticks; `--diff K` lists up to `K` differing runs per register.

| module | content |
|---|---|
| `sidwizard.py` | `Transcriber`: build checks, instruments, events, timeline, document |
| `swtables.py` | `Tables` mixin: WF, pulse and filter tables as programs |
| `swcode.py` | the 6502 code templates and their per-cell `state` |
| `swtrace.py` | `trace`: the reference player's row reads; `standard`: its note table |

## Time

Every tick is a step (`tempo: 1`). Row timing is not computed: `swtrace.trace` hooks the
reference player's row read, row apply, deferred FX, SETFLTP and staccato hard restart, and a
row lasts from its read to the voice's next read (tempo, funk tempo and tempo FX included).

| SID-Wizard | generic |
|---|---|
| a row read at frame `f`, the next at `f + n` | `n` events: TICK_0, TICK_1, TICK_2, then `-` |
| the orderlists' pattern starts | the timeline: a row where some voice starts a pattern, `~` elsewhere |
| orderlist end | `end` on that voice |
| orderlist loop | `{loop: k}`, the earliest row from which the timeline repeats |
| loop detection | every voice ended, or its pattern starts repeated 4 times their period's lcm (16, 64 if the timeline does not yet repeat; at most 200000 frames) |
| `init` | each voice's dummy instrument, `vol: 15` |

## A row's events

| tick | event |
|---|---|
| TICK_0 | a note's hard restart: `pw: hold`, `cut: hold` (`pm.freq: none` with the mirror); HRGTOFF's `off` and HR `ad`/`sr` (`ctrl: $18` staccato) where its hard-restart type fires; a voice's first note runs its dummy instrument's WF program; `hr.freq: hrc(c)` where WF rows hold |
| TICK_1 | HRGTOFF where it fires on TICK_1; `cut`/`walk.cut: frs()` for the filter voice's note with keyboard tracking |
| TICK_0/1 staccato skip | `run` of a program resuming the tables where the player left them (`tick.freq: wt()` where VIBSLIDE runs every tick) |
| TICK_2 | the row applied: note, instrument, FX |

A binding already held keeps its phi: TICK_1 rebinds `tick.freq: stp()`, `fx.cut: none` and
`late.cut: none` when TICK_2 binds them.

| TICK_2 row | event |
|---|---|
| a note (STRTSND) | the note, `.iN` where it changes; `run` of the WF program keeping a reset-switch pulse or filter walk, or of the filter taken over mid-sweep |
| legato `$3F`, BIGFX03, a note after NPORTAM | `!legato`, `tick.freq: leg()`/`por(m,…)`/`npn()`, `run` resuming the tables past TICK_2 |
| `$7E` | `off` (with a gate-off pointer: `run` resuming from it) |
| `$7D`, `$79`-`$7C` | `on`/`-` with the control byte WFGHOST holds |
| `$60`-`$77` | `tick.freq: vfx(…)` |
| `$78` NPORTAM | `tick.freq: sld(255, …)` |
| an instrument without a note | `run` of its tables resumed from where they are |

## FX

| FX | commands |
|---|---|
| small `$2x` `$3x` `$5x` `$6x` | `ad`, `sr` as applied |
| small `$4x`, BIGFX04 | `ctrl` |
| small `$7x`, BIGFX07 | `run` of the WF walk on the new chord (chord builds) |
| small `$8x`, BIGFX08 | `tick.freq: vfx(…)` |
| small `$9x` | `tick.freq: vfq(q)` |
| small `$Ax`, `$Bx` | `vol`, `mode` |
| small `$Cx`, BIGFX0C | `run` of the WF walk at the new arp speed |
| small `$Dx`, BIGFX0D | `dt.freq: dfx(d)` (detune builds) |
| BIGFX01, 02 | `tick.freq: sld(129/130, …)` |
| BIGFX03 | `tick.freq: sld(131, …)` without a note, `por` with one |
| BIGFX05, 06 | `ad`, `sr` |
| BIGFX0F | `fx.cut: fhi(c)` before the filter walk (a voice after the filter's), else `late.cut` |
| BIGFX16 | `tick.freq: kty(c)` |

## Instruments and programs

| SID-Wizard | generic |
|---|---|
| instrument `n` | `i<n>`; `i<n + 64(voice + 1)>` with a first frame by track; dummies `i256`… |
| AD, SR | `ad`, `sr` |
| STRTSND | `snd.freq: snd(c,d,i,q,h,t,r/g)`, `tick.freq: stp()` |
| WF table | `ctrl`: `{ctrl: 9 or the first waveform, route_on: 0}` (`route_off: 0` unrouted), then a row per read held its ARPSCNT ticks; arp as `note` (`+n`, `-n`, absolute) with `wave.freq: tone()`; detune and carry as `dt.freq: det(f,d,c,b)`; chord rows a read per chord pitch; `$FE` jumps taken on the tick, `{loop: k}`; the walk at rest re-read at its pace |
| pulse table | `pw`: set `{pw: w}`, sweep `{hold: n, pw: v + d}`, its advance tick; with keyboard tracking `pst`/`psw`/`pid`/`pwr` |
| filter table (claimed) | `filter`: set `{res, mode, cut}`, sweep `{hold: n, cut: v + 8d}` (`v + d` FINEFILTSWEEP), advance tick; with keyboard tracking or BIGFX0F `fst`/`fsw`/`fid`/`frs` (`fho` a handover) |
| a resumed table (legato, switch, staccato, reset switch) | a program from the player's position and sweep count |
| the dummy instrument | instrument 0's header walked as two WF rows (and its pulse table where VIBSLIDE runs every tick) |

## Code

`STATE` per cell:

| cell | state | SID-Wizard |
|---|---|---|
| freq | `o` | vibrato_offset |
| | `s` | a WF row's pitch drives the frequency |
| | `z` | FREQLO/FREQHI |
| | `dc`, `vc`, `p`, `fm`, `k` | VIDELCNT, VIBRACNT, VIBFREQU, FREQMOD, SLIDEVIB |
| | `dd`, `cm`, `ca`, `cv` | DETUNER, WRPITCH's carry mode, arp, HARDRST carry |
| | `ml`, `mk`, `cs`, `mt` | the pulse mirror: PWLOGHO/HIGHO, PKBDTRK, WRPULS's carry, scratch |
| | `vt`, `vi`, `vq`, `vr`, `vg` | the instrument vibrato PORTAVIBRA's end restores |
| pw | `pl`, `pk` | PWLOGHO/PWHIGHO, PKBDTRK |
| cut | `ch`, `cl`, `ck` | CTFH, CTFL, the filter's keyboard track |

Slots, in the player's order:

| cell | slots |
|---|---|
| freq | `snd`; `tick` (hold `note-2`, `note-1`, `note`; `note` where VIBSLIDE runs every tick); `wave` (hold `note`, `legato`, reset `row.note`); `hr` (WF rows held); `pm` (pulse mirror); `dt` (detune builds, reset `row.freq`) |
| cut | `fx`, `walk`, `late` (BIGFX0F tunes) |

| template | routine |
|---|---|
| `stp` | VIBSLIDE: dispatch on SLIDEVIB to vibrato, slide up/down, portamento, NPORTAM; built per build from CALCVIBRATO (SETFMOD from the note, else constant `g`), VIBRATOTYPES (INCVIBR), PORTAVIBRA (PORTEND restores `vt`…`vg`) |
| `snd` | STRTSND: SLIDEVIB, SETVIB0/SETVIBR, detune off, FREQHI by the first-frame switch |
| `wt` | `stp` from the next tick (a staccato tick) |
| `vfx`, `vfq`, `kty`, `sld` | FORCVIB/SETVIBR, VIBFREQU, SLIDEVIB, BIGFX01/02/03/NPORTAM, then `stp` |
| `por`, `leg`, `npn` | toward the note's word (BIGFX03, legato with FREQMODH `$7F`, after NPORTAM) |
| `tone` | FREQLO/FREQHI of the WF note; a pitch row clears `o` |
| `det`, `dfx` | WRPITCH: detune and the carry of the WF read (mode 3: pulse carry `cs`) |
| `hrc` | HARDRST's carry on a note's skipped WF ticks |
| `pst`, `psw`, `pid`, `pwr` | SETPULW, PWSWEEP step, PWADVAN, WRPULS with `EXPTABH[(DPITCH & $7F) + PKBDTRK]` |
| `mst`, `msw`, `mid`, `mwr` | the pulse templates on the freq cell, keeping only WRPULS's carry in `cs` |
| `fst`, `fsw`, `fid`, `frs` | SETFILT, FILTPRG step (11-bit with FINEFILTSWEEP), advance, the cutoff write with its track |
| `fho`, `fhi` | a filter handover's cutoff; BIGFX0F's CTFH |

The pitch is the standard note table `F` at `clock_hz: 985256`: generic note `k` is SID-Wizard
note `k + 1`, and the reference player runs at that table (its EXPTABH built on it).

## Refused

`TranscribeError` (or the trace's `TraceError`); counts over the 182 single-subtune SID-Wizard
tunes of the corpus at 3000 frames (109 exact, 0 differing):

| cause | tunes |
|---|---|
| WF rows held over ticks with a pulse table, its carry | 10 |
| multispeed, a play gate or an initial filter | 15 |
| multi-SID (pysidwizard `SIDFormatError`) | 8 |
| an instrument selected before any note | 7 |
| a cut jump to a jump or an end | 4 |
| an instrument selected without a note on a row that runs a table | 4 |
| chord past the chord table or empty | 4 |
| FX `$09`, `$0B`, `$1C` | 5 |
| a note sounding below note 0 | 4 |
| no loop within 200000 frames | 2 |
| an arp speed FX on a note or before any instrument | 2 |
| a filter handover on a note that runs another table | 2 |
| an instrument of another octave selected without a note | 2 |
| a legato note with a vibrato FX | 1 |
| WF rows held over ticks without STRTSND's ARPSCNT reset | 1 |
| two FX of one cell on a row | 1 |
| a first frame that keeps a waveform no hard restart gated off | 1 |

Also refused (no corpus tune): FX `$0A`, `$0E`, `$17`-`$1B`, `$1F`, small FX `$Fx`; other
note-column values; a detune FX on a note; a waveform FX on a note or gating a voice gated
off; a gate-off pointer on a row that runs another table; a WF jump chain; a pulse or filter
table that starts with a sweep where no count is left; a timeline that does not repeat; no
instruments or fewer orderlists than voices.
