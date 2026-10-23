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
| realtime commands | tick 0 (`optimize_realtime`), and the tick 0 of a new note | `freq: step-1` (`note-1` without the option) |
| realtime commands | a wavetable row that plays a note or a command (`skip_tickn`) | `freq: row` |
| pulse table | the fetch tick (`optimize_pulse`) | `pw: read` |
| pulse table | tick 0 of a new note that is not toneporta | `pw: note-1` |
| pulse table | tick 0 of a pattern's last row, where the sequencer advances (`optimize_pulse`) | `pw: last-1` |

## Instruments

| GoatTracker | generic instrument |
|---|---|
| AD, SR | `ad`, `sr` |
| wavetable position | `ctrl`, the wavetable program (`c` of no rows for 0) |
| pulse table position | `pw`, the pulse expression; 0 leaves the running one |
| filter table position | `filter`, the filter program; 0 leaves the running one |
| vibrato param, delay | `freq`: `G[N]`, plus the vibrato after `delay - 1` firings |
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
| wavetable `$F5/$F6/$F7/$F9/$FA/$FB/$FC/$FD` | `ad`, `sr`, `ctrl`, `run`/`pw: none`, `run`/`filter: none`, `res`+`route`, `cut`, `vol` |
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
| `0` after a frequency command | `!freq:hold` |
| `1`, `2` | `!freq:x`: `v ± s`, `s` the speed entry or `(G[N+1] - G[N]) >> r` |
| `3` with a note | `!freq:x` toward `G[N]`, and `!legato` |
| `4` | `!freq:x`: `v ± s` by the sign bit of the vibrato phase, a table over its orbit |
| `5`, `6`, `7` | `!ad`, `!sr`, `!ctrl` |
| `8`, `9`, `A` | `!run:c`/`!run:none`, `!run:pw`/`!pw:none`, `!run:f`/`!filter:none` |
| `B` | `!resfilt:r/m`, `!filter:none!resfilt:0/0` for 0 |
| `C`, `D` | `!cut:8c`, `!vol` |
| `F` | `!tempo` (`!vtempo` for `$80+`) |

A frequency command starting on a new note is `G[N]` before its first firing (`φ < 0`), as the
wavetable's note row sets the frequency on the tick the command skips. A phrase is a pattern and
the frequency command running into it: the same command continues without restarting `φ`.

## Not transcribed

Funk tempo (command `E`, and `F` below 2), wavetable commands `$F0-$F4` and `$F8`/`$FE`, and a
filter table started by a second voice while another voice's runs (GoatTracker's filter is one
program; the document's filter slots are per voice).
