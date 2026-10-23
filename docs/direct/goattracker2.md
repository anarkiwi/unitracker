# GoatTracker 2 to the generic document, directly

`unitracker.direct.goattracker2` transcribes a GoatTracker 2 SID to the generic document from
pygoattracker's playroutine (`pygoattracker.player.Player`, a tick-for-tick transcription of
`gplay.c`/`player.s`), without the playroutine library or modfit. It is a standalone prototype:
nothing else in unitracker calls it.

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
| init call, then the pre-roll step | `init` (`!tempo:2`) and phrase `p0`: `[-, -!tempo:T]` |
| the sequencer's pattern starts | the timeline: a row where some voice starts a pattern, `~` elsewhere |
| orderlist transpose | the play entry's `+t` |
| orderlist loop | `{loop: k}`, the earliest row from which the timeline repeats |

## Holds

`_play_channel` skips the realtime command and the pulse table on fixed ticks; each skip is a
voice event, so each is a hold.

| skip | condition | hold |
|---|---|---|
| realtime commands | tick 0 (`optimize_realtime`), and the tick 0 of a new note | `tick.freq: step-1` (`note-1` without the option) |
| realtime commands | a wavetable row that plays a note or a command (`skip_tickn`) | `tick.freq: row` |
| pulse table | the fetch tick (`optimize_pulse`) | `pw: read` |
| pulse table | tick 0 of a new note that is not toneporta | `pw: note-1` |
| pulse table | tick 0 of a pattern's last row, where the sequencer advances (`optimize_pulse`) | `pw: last-1` |

## Frequency

`_play_channel` writes the frequency twice a tick, in order: `_wave_exec` (a wavetable note row
sets it, a wavetable command steps it), then `_tickn_command` (the realtime command, or the
instrument's vibrato with command 0). The document declares them as the frequency's two slots in
that order, `slots: {freq: [wave, tick]}`: each fires on the value the one before it left, and a
held slot passes it on.

| writer | slot | modulator |
|---|---|---|
| wavetable note row | `wave.freq` | `G[N]` once, then `v` |
| wavetable `$F1`/`$F2`/`$F3` | `wave.freq` | one step of the portamento, then `v` |
| a note's instrument | `wave.freq`, `tick.freq` | `v`; its vibrato after `delay - 1` firings |
| pattern commands 1-4 | `!tick.freq:x` | `v ± s`, toward `G[n]` (`n` the event note), vibrato from the row's phase |
| command 0 after a frequency command | `!tick.freq:x`, or `!tick.freq:none` without a vibrato | the instrument's vibrato from the row's phase and delay left |

## Instruments

| GoatTracker | generic instrument |
|---|---|
| AD, SR | `ad`, `sr` |
| wavetable position | `ctrl`, the wavetable program (`c` of no rows for 0) |
| pulse table position | `pw`, the pulse expression; 0 leaves the running one |
| filter table position | `filter`, the filter program; 0 leaves the running one |
| vibrato param, delay | `tick.freq`: the vibrato after `delay - 1` firings |
| gate timer, hard restart flags `$40`/`$80`, `adparam` | `restart`: `{hold: timer, gate: 0, ad, sr}` |
| first wave | `restart`'s tick-0 row: `ctrl`, `gate: 1`, `ad`, `sr`, and `run` of the empty `c`, as tick 0 does not run the old wavetable |

The note's frequency is `G[N]` over `tables: G`, the player's own frequency table (the packed
player's overrun entries included).

## Tables

| table | generic |
|---|---|
| wavetable `$01-$0F` delay | `wait` |
| wavetable `$10-$DF`, `$E0-$EF` | `ctrl` (`& $0F` for `$Ex`) |
| wavetable note `$00-$7F`, `$81-$DF`, `$80` | `note: +n` (`$40-$7F` down), absolute `note`, none |
| wavetable `$F1/$F2/$F3` | `wave.freq`, one step |
| wavetable `$F5/$F6/$F7/$F9/$FA/$FB/$FC/$FD` | `ad`, `sr`, `ctrl`, `run`/`pw: hold`, `run`/`cut: hold`, `res`+`route`, `cut`, `vol` |
| wavetable `$FF xx` | `{loop: k}`, or `end` for `xx = 0` |
| pulse table | one expression of `φ` (the firing count, by the holds): per firing a set width or an added step, from `_pulse_exec`, `P[i]` (and `K[i]` where both occur) over the orbit's prefix and cycle |
| filter table `$80-$F0` (+ a following `$00`) | `{mode, res, route, cut}` in one row |
| filter table `$01-$7F` | `{hold: t, cut: v + 8s}` |
| filter table `$00` | `{cut: 8c}` |

The filter table runs on every tick, so its rows are program rows; the pulse table does not, so
its value is a function of its firings.

## Pattern commands

| command | generic |
|---|---|
| `0` after a frequency command | `!tick.freq:x` (the instrument's vibrato) or `!tick.freq:none` |
| `1`, `2` | `!tick.freq:x`: `v ± s`, `s` the speed entry or `(G[N+1] - G[N]) >> r` |
| `3` with a note | `!tick.freq:x` toward `G[n]`, and `!legato` |
| `4` | `!tick.freq:x`: `v ± s` by the sign bit of the vibrato phase, a table over its orbit |
| `5`, `6`, `7` | `!ad`, `!sr`, `!ctrl` |
| `8`, `9`, `A` | `!run:c`/`!run:none`, `!run:pw`/`!pw:hold`, `!run:f`/`!cut:hold` |
| `B` | `!resfilt:r/m`, `!resfilt:0/0!cut:hold` for 0 |
| `C`, `D` | `!cut:8c`, `!vol` |
| `F` | `!tempo` (`!vtempo` for `$80+`) |

A phrase is a pattern occurrence's events; the same command running into it continues without
restarting `φ`.

## Context

Each pattern occurrence is written from the reference player's channel state at each row's
tick 0: the frequency command running into it, its instrument, and the vibrato phase and the
delay left. Occurrences whose events come out the same share a phrase. A vibrato command starts
from that phase; command 0 after a frequency command resumes the instrument's vibrato from it.

## Not transcribed

Wavetable vibrato (`$F4`) and the commands that stop the song (`$F0`, `$F8`, `$FE`) are refused.

These play differently from GoatTracker because the document has no construct for them:

| GoatTracker | why the document differs |
|---|---|
| one filter program for the song | a document's filter slot is per voice: a second voice starting a filter program leaves the first voice's running |
| a toneporta note's note change on tick 0 | a `!legato` note applies on the step's tick, GoatTracker's tick 1 |
