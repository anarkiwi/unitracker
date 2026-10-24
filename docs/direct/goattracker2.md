# GoatTracker 2 to the generic document, directly

`unitracker.direct.goattracker2` transcribes a GoatTracker 2 SID to the generic document from
pygoattracker's playroutine (`pygoattracker.player.Player`, a tick-for-tick transcription of
`gplay.c`/`player.s`), without the playroutine library or modfit. It is a standalone prototype:
nothing else in unitracker calls it. It builds the document with `unitracker_grammar.document`'s
classes and writes it with its `printed`: the templates, instruments, control and filter programs
as top-level sections, the phrases among them.

```sh
python -m unitracker.direct.goattracker2 TUNE.sid > tune.yaml
python -m unitracker.direct.goattracker2 --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with the
reference player's (`decompile_sid(raw).player()`) over `--frames` ticks; `--diff K` lists up to
`K` differing runs per register.

## Time

| GoatTracker | generic |
|---|---|
| a pattern row of `tempo + 1` ticks | a step; it begins on the row's tick 1, when a new note sounds |
| tick 0 (new note init, tick-0 commands) | the restart's last lookahead row, one tick before the step |
| the fetch tick (`tick == gatetimer`) | the restart's first row, `gatetimer` ticks before tick 0 |
| init call, then the pre-roll step | `init` (`tempo:2`) and phrase `p0`: `[-,- tempo:T]` |
| the sequencer's pattern starts | the timeline: a row where some voice starts a pattern, `~` elsewhere |
| orderlist transpose | the play entry's `+t` |
| orderlist loop | `{loop:k}`, the earliest row from which the timeline repeats |

## Order

`_play_channel` runs a voice's tick as tick-0 work, the wavetable, the realtime command, the
pulse table, then the fetch, where a hard restart turns the gate off and writes its AD/SR. The
document declares that order, `order:[events,ctrl,pulse,filter,freq,pw,cut,read]`: a
restart's first row (the fetch) applies after the voice's programs, so it overwrites a wavetable
row's AD or SR on the same tick.

## Holds

`_play_channel` skips the realtime command and the pulse table on fixed ticks; each skip is a
voice event, so each is a hold.

| skip | condition | hold |
|---|---|---|
| realtime commands | tick 0 (`optimize_realtime`), and the tick 0 of a new note | `tick.freq:step-1` (`note-1` without the option) |
| realtime commands | a wavetable row that plays a note or a command (`skip_tickn`) | `tick.freq:row` |
| pulse table | the fetch tick (`optimize_pulse`) | `pw:read` |
| pulse table | tick 0 of a new note that is not toneporta | `pw:note-1` |
| pulse table | tick 0 of a pattern's last row, where the sequencer advances (`optimize_pulse`) | `pw:last-1` |

## Frequency

`_play_channel` writes the frequency twice a tick, in order: `_wave_exec` (a wavetable note row
sets it, a wavetable command steps it), then `_tickn_command` (the realtime command, or the
instrument's vibrato with command 0). The document declares them as the frequency's two slots in
that order, `slots:{freq:[wave,tick]}`: each fires on the value the one before it left, and a
held slot passes it on.

| writer | slot | modulator |
|---|---|---|
| wavetable note row | `wave.freq` | `note` |
| wavetable `$F1`/`$F2`/`$F3` | `wave.freq` | `porta1`/`portan1`, `tporta1`/`tportan1`/`snap1` |
| a note's instrument | `wave.freq`, `tick.freq` | `note`; `vib`/`vibn` after `delay - 1` firings |
| pattern commands 1-4 | `tick.freq:call` | `porta`/`portan`, `tporta`/`tportan`/`snap`, `vib`/`vibn` |
| command 0 after a frequency command | `tick.freq:call`, or `tick.freq:none` without a vibrato | the instrument's `vib`/`vibn` after the delay left |

### Code modulators

The modulators are format 2.3 6502 code templates
([`gt2code.py`](../../src/unitracker/direct/gt2code.py)), each a transcription of the
playroutine function it replaces. The top-level `mod:` section, after `tables:`, holds those
the song uses by signature (`vib(l,c,s):|`) in first-use order, and `state:` the variables
they name; a binding is a call, its arguments in signature order (`tick.freq:vib(0,3,48)`,
`tick.freq:vib(7,1,16)`, `pw:ptab(1)`, `wave.freq:note()`). The freq cell's state is GoatTracker's channel state:

| state | GoatTracker | written by |
|---|---|---|
| `t` | `vibtime`, the vibrato phase | `note` (0), `vib`/`vibn`, a toneporta reaching its note (0) |
| `m` | `lastnote`, the note calculated speeds read | `note` (`N & 127`), a toneporta reaching its note (`n`) |

| template | params | routine |
|---|---|---|
| `note` | | on `phi = 0`: `v = F[N & 127]`, `t = 0`, `m = N & 127`; then `v` |
| `porta` | `g` (1 up, -1 down), `s` | `v ± s` |
| `portan` | `k`, `g` | `v ± ((F[m + 1] - F[m]) >> k)` |
| `tporta`, `tportan` | `s`; `k` | `_toneporta`: toward `F[n & 127]` by `s` (or the calculated step), snapping to it with `t = 0`, `m = n` where it crosses |
| `snap` | | speed entry 0: `v = F[n & 127]`, `t = 0`, `m = n` |
| `vib`, `vibn` | `l`, `c`, `s`; `l`, `k`, `c` | after `l` firings, `_vibrato`: the phase `t` turns at depth `c` and steps by 2, `v ± s` (or the calculated step) by its low bit |
| `porta1`, `portan1`, `tporta1`, `tportan1`, `snap1` | as above | the same on `phi = 0` only (a wavetable command row) |

A speed table entry `(l, r)` is `s = l << 8 | r`, or with `l & $80` calculated: `k = r` (and
`c = l & $7F` for a vibrato).

## Instruments

| GoatTracker | generic instrument |
|---|---|
| AD, SR | `ad`, `sr` |
| wavetable position | `ctrl`, the wavetable program (`c` of no rows for 0) |
| pulse table position | `pw`, the pulse expression; 0 leaves the running one |
| filter table position | `filter`, the filter program; 0 leaves the running one |
| vibrato param, delay | `tick.freq`: the vibrato after `delay - 1` firings |
| gate timer, hard restart flags `$40`/`$80`, `adparam` | `restart`: `{hold:timer,gate:0,ad,sr}` |
| first wave | `restart`'s tick-0 row: `ctrl`, `gate:1`, `ad`, `sr`, and `run` of the empty `c`, as tick 0 does not run the old wavetable |

The note's frequency is `F[N]`, the player's built-in note table read as `F.lo`/`F.hi`, at
`clock:{cycles_per_tick:19656,clock_hz:985000}`: GoatTracker's editor table is exactly
`F(k)` at 985000 Hz for notes 0-94.

## Tables

| table | generic |
|---|---|
| wavetable `$01-$0F` delay | `wait` |
| wavetable `$10-$DF`, `$E0-$EF` | `ctrl` (`& $0F` for `$Ex`) |
| wavetable note `$00-$7F`, `$81-$DF`, `$80` | `note:+n` (`$40-$7F` as `n - 128`, down), absolute `note`, none |
| wavetable `$F1/$F2/$F3` | `wave.freq`, one step |
| wavetable `$F5/$F6/$F7/$F9/$FA/$FB/$FC/$FD` | `ad`, `sr`, `ctrl`, `run`/`pw:hold`, `run`/`cut:hold`, `res`+`route`, `cut`, `vol` |
| wavetable `$FF xx` | `{loop:k}`, or `end` for `xx = 0` |
| pulse table from row `b` | `ptab {b}` (`ptabs` with `SIMPLEPULSE`), `_pulse_exec` over the player's own table in `PL`/`PR` (`PL[k]`, `PR[k]` its row `k`); the pw cell's state `r` is the row and `q` the time left (on `phi = 0`, `r = b`, `q = 0`): a jump row moves `r`, a set row writes the width, a modulation row adds its signed step |
| filter table `$80-$F0` (+ a following `$00`) | `{mode,res,route,cut}` in one row |
| filter table `$01-$7F` | `{hold:t,cut:v + 8s}`, `s` the signed step (`v - 8` for `$FF`) |
| filter table `$00` | `{cut:8c}` |

The filter table runs on every tick, so its rows are program rows; the pulse table is held on
the playroutine's skips, so it is a code modulator of the pw slot.

## Pattern commands

| command | generic |
|---|---|
| `0` after a frequency command | `tick.freq:call` (the instrument's `vib`/`vibn`) or `tick.freq:none` |
| `1`, `2` | `tick.freq:call`, `porta` or `portan` with `g` 1 or -1 |
| `3` with a note | `tick.freq:call`, `tporta`, `tportan` or (`0`) `snap`, and `legato` |
| `4` | `tick.freq:call`, `vib` or `vibn` with `l` 0 |
| `5`, `6`, `7` | `ad`, `sr`, `ctrl` |
| `8`, `9`, `A` | `run:c`/`run:none`, `pw:ptab(b)`/`pw:hold`, `run:f`/`cut:hold` |
| `B` | `res:r route:m`, `res:0 route:0 cut:hold` for 0 |
| `C`, `D` | `cut:8c`, `vol` |
| `E` | `tempo:a/b`, the speed entry's two lengths |
| `F` | `tempo` (`vtempo` for `$80+`) |

A phrase is a pattern occurrence's events; the same command running into it continues without
restarting `phi`.

## Context

Each pattern occurrence is written from the reference player's channel state at each row's
tick 0: the frequency command running into it, its instrument, and the vibrato delay left.
Occurrences whose events come out the same share a phrase. The vibrato phase is the state `t`,
so command 0 after a frequency command resumes the instrument's vibrato after the delay left.

## Not transcribed

Wavetable vibrato (`$F4`) and the commands that stop the song (`$F0`, `$F8`, `$FE`) are refused.

The build's played channels (those not in `Build.idle`) become the document's voices, in the
order of the SID voices `Build.voices` writes them to (`voices:` where fewer than three). Builds
whose played channels do not fill the first SID's voices from voice 1, reach a second SID or
no voice, and multispeed builds (`Build.calls` above 1) are refused.

These play differently from GoatTracker because the document has no construct for them:

| GoatTracker | why the document differs |
|---|---|
| one filter program for the song | a document's filter slot is per voice: a second voice starting a filter program leaves the first voice's running |
| a toneporta note's note change on tick 0 | a `legato` note applies on the step's tick, GoatTracker's tick 1 |
| command 9 (or a wavetable `$F9`) setting the pulse program already running | it restarts the table; a binding to the modulator a slot is bound to keeps its phi |
| a ghosting build (the SID written from the previous call's registers) | its output lags the playroutine one tick and starts from the image's registers |
| the SID registers a build's init leaves from its image before the first notes | the document's registers start at 0 |
| the packed frequency table's overrun entries (notes 95-127: B7's `$FFFF` and the bytes past the table) | `F(k)` is 0 there |
