# GoatTracker 2 (`goattracker2`)

The GoatTracker 2 playroutine (`player.s`, buffered writes) of
[`goattracker2.yaml`](../../src/unitracker/playroutines/goattracker2.yaml). The VM's
canonical flush order (see [Ticks, the ghost and the canonical flush](schema.md#ticks-the-ghost-and-the-canonical-flush))
is this player's buffered order (`BUFFEREDWRITES`: the filter step, then `mt_loadregs`).

## Lifting

| | |
|---|---|
| tune | a packed GoatTracker 2 `.sid`, decompiled by pygoattracker |
| lifted from | the decompiled song, and the build's hard-restart preset `adparam` as the restart programs' `ad` and `sr` |
| tempo | the tempo cells the native player starts each voice on, read after its init: a tempo `t` from 2 is `t + 1` ticks, and 0 and 1 the swing on its funk pair from its first or second tempo. The playroutine's `tempo` starts 2-129 as `!tempo` does: 3-127 in the tempo cell, and 2, 128 and 129 on the funk pair, both that tempo |
| clock | NTSC when the header's clock flags say so, else PAL |
| step | one pattern row; the playroutine counts ticks per row itself, and `fetch` runs on the gate-timer lookahead, the hold of the restart's first row |
| notes | `note_offset` 0 |
| onset | the tick a wavetable row first sets the voice's frequency: on its first note's first tick the voice still sounds the frequency it had before the note |

## Records

| record type | native table | notes |
|---|---|---|
| `ins` | instruments | keys `ad`, `sr`, `ctrl`, `pw`, `filter`, `freq`, `restart`; instrument 1 is pinned, because every voice starts on it |
| `ctrl` | wavetable (`ctrltab`) | see Control programs |
| `pw` | pulse table (`pulsetab`) | written as the program's `pw` expression |
| `filter` | filter table (`filttab`) | see Filter |
| `restart` | the instrument's gate timer and first wave | instruments with one gate timer and first wave share a restart program |

A jump row ($FF, target) chains the row before it to its target. The player follows one jump
and reads the row it lands on as data, so a jump to a jump row runs that row's bytes: in the
wavetable a row that keeps the waveform and plays its target byte as a note, in the pulse table
the width $F00 plus its target byte, in the filter table a parameter row of mode $70 setting
`$D417` to it.

The speed table has no written form: its vibrato and slide speeds are terms of the `freq`
expressions, and `!tempo` and `!vtempo` write a swing's row inline.

## Events

`.i3` names the instrument. An event carries one command and `!legato`: `!legato` is in the
instrument column, every other command in the FX column (`!filter:none!resfilt:0/0` is one
command, $B00, see [Filter](#filter)).

## Commands

| command | argument |
|---|---|
| `!run` | `ctrl` name, `pw` name, `filter` name, `none` |
| `!pw` | `none` |
| `!filter` | `none` |
| `!ad` | 0-255 |
| `!sr` | 0-255 |
| `!ctrl` | 0-255 |
| `!resfilt` | 0-15/0-15 |
| `!vol` | 0-15 |
| `!cut` | 0-2040 step 8 |
| `!tempo` | 2-129, 2-129/2-129 (two different) |
| `!vtempo` | 2-129, 2-129/2-129 (two different) |
| `!freq` | `x` name, `none` |
| `!legato` | no argument |


- `!tempo:n` and `!vtempo:n` are command $F from 3 to 127; it plays a tempo of 2 as 3, so 2, 128
  and 129 are written as `!tempo:a/b` and `!vtempo:a/b` are, with the row `n/n`.
- `!tempo:a/b` is command $E, its argument the speed-table row's two bytes, 2-129 ticks each.
  Each voice keeps its own swing pair: `!tempo:a/b` sets every voice's, and `!vtempo:a/b`,
  playroutine command 18, this voice's. The native player keeps one pair, which only $E and its
  init set, so every voice's pair is that one in a native song. The lifter writes $E00 and
  $F00/$F01/$F80/$F81, which swing on the pair already set, as the commands that name the pair
  each meets where it plays ($x1 reversed, starting on its second tempo): a phrase that meets
  different pairs at different places becomes one phrase per place, and a loop whose passes meet
  different pairs is written out from where they repeat.
- `!vol` is command $D below $10. From $10 the command plays nothing but keeps the voice's
  continuous command running; the lifter writes it as that command, or as an empty step when
  there is none, once a replay proves the song unchanged.
- `!run:none`, `!pw:none` and `!filter:none` are commands 8, 9 and 10 with argument 0, which stop
  the control, pulse and filter programs.
- `!legato` is command 17: the lifter writes a native toneporta on a note as `!legato` plus
  the toneporta, so the note does not retrigger. A toneporta of speed 0 sets the frequency to the
  note's on every tick, which is not `!legato` alone (that leaves the frequency where it was); one
  the expression conversion leaves is written `!freq` of the expression `0`, the note's frequency,
  once a replay proves the song unchanged.
- `!freq` is command 16, which plays every expression; `!freq:none` is 16 with 0 (see
  Modulators).

## Control programs

| `ctrl` row key | forms |
|---|---|
| `wait` | value |
| `ctrl` | value |
| `note` | value |
| `ad` | value |
| `sr` | value |
| `pw` | `none` |
| `cut` | value |
| `res` | value |
| `route` | value |
| `freq` | expression |
| `run` | `pw` name, `filter` name |
| `filter` | `none` |
| `vol` | value |

A wavetable delay row is `wait` on the row it delays, and a jump is the program's `loop`. A
command row runs the pattern command of its low nibble, and the tick's continuous effect does not
run, as on a note row:

| row | written as |
|---|---|
| $F1-$F4 | `freq`: one portamento, toneporta or vibrato step (see Modulators) |
| $F5, $F6 | `ad`, `sr` |
| $F7 | the waveform row `ctrl` of its value, when a replay proves its pause of the continuous effect unheard |
| $F9, $FA | `run` (the pulse or filter program); with 00, `pw: none` and `filter: none` |
| $FB | `res` and `route`; with 00, `{res: 0, route: 0, filter: none}` (see Filter) |
| $FC | `cut` |
| $FD below $10 | `vol` |
| $FD $10-$FF | the empty row `{}`, when a replay proves its pause unheard |

## Filter

| keys | set by |
|---|---|
| `res`, `route` (a mask) | filter parameter rows, which set both; `!resfilt` |
| `mode` | filter parameter rows, bits 4-6 |
| `cut` | the filter program's `cut` expression; a parameter row carries the following cutoff row's value, because the player reads both in one tick; `!cut` |
| `hold` | a cutoff sweep row of speed 0 (`l` $01-$7F, `r` 0), which holds the cutoff, or the expression driving it, `l` ticks; the expression conversion clears each sweep row it replaces to one, keeping `l`, and a set-cutoff row (`l` 0) it replaces to one of `l` 1, which, like the set-cutoff row, lasts one tick |
| volume | `!vol` |

`res` and `route` are one byte, which a filter parameter row, `!resfilt:r/m` (command $B) and a
wavetable $FB row set whole; from the command or the wavetable row, the byte 0 also stops the
filter program, and is written so: `!filter:none!resfilt:0/0` (one command $B00) and `{res: 0, route: 0, filter: none}`. A pattern command or wavetable row that sets the cutoff, `res`/`route`
or the volume ($B-$D, $FB-$FD) sounds on the following tick: the filter step writes
`$D416`-`$D418` before the channels run, so the playroutine latches them there (`fcutreg`,
`fctrlreg`, `fmodereg`).

## Hard restart

| lookahead row | first-tick row |
|---|---|
| exactly one: `hold` 1-63 (the gate timer); `gate: 0` or nothing; with `gate: 0`, `ad` and `sr` or neither | `{ctrl: n}` (n $01-$FD, the first wave, which sets the gate on), `{gate: g}`, or `{}` |

The player reads the voice's next step its instrument's gate timer ahead of each note; an
instrument without a restart reads it on the note's first tick. A note keeps the gate as the
restart left it, so its first-tick row sets it. The build has one envelope preset (`adparam`),
so every lookahead row that sets `ad` and `sr` sets the same values (`uniform`). A note's set-AD and set-SR
(commands 5 and 6) run on its first tick after it loads the instrument's envelope, as its `!ad`
and `!sr` play with the first-tick row ([Hard restart](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#hard-restart)).

## Lifetime

A note starts the instrument's `pw` and `filter` programs, and wavetable `run` rows ($F9, $FA)
start them where the composer put them; the playroutine declares no `lifetime`, so no event
decodes as `!pw` or `!filter` without an argument.

## Modulators

`F(k)` is `ftab[k]`; `N` is `note`.

| unit | key | cell | value `v` | fires | starts | written |
|---|---|---|---|---|---|---|
| `command` | `freq` | `freq` | the offset from `F(N)`, the frequency of the step's note: a wavetable note row sets `freq` to its own note's frequency and the command does not run that tick, so commands 1-4 work from the step's note, not the arpeggio's | every tick of a step but tick 0, except ticks a wavetable row plays a note or runs a command | the step's command or argument changes, a note starts, or a wavetable row plays a note | `!freq:x` (command 16); without a command, the instrument's `freq` |
| `wave` | `freq` | `freq` | the offset from the frequency of the sounding note (with the wavetable's arpeggio) | once, on the tick its wavetable row runs | every run of a wavetable step row | the row's `freq` |
| `pulse` | `pw` | `pulse` | the cell | each pulse-program tick: not on the tick the voice reads its next step, nor tick 0 of a step that starts a phrase | an instrument, `!run:pw` or a wavetable $F9 sets the program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `filtercutoff`, global | the 11-bit cutoff, `filtercutoff << 3` | each filter-program tick | an instrument, `!run:f`, `!filter:none!resfilt:0/0` or a wavetable $FA/$FB sets the program | the program's first row (instrument `filter`) |

- `!freq` is command 16 with `lasts: step` (see [Lifetime](schema.md#lifetime)); `!freq:none`
  is command 16 with 0, which plays as a step without a command, and the same `!freq` on the
  next step continues the occurrence (`φ` keeps counting). A note's steps without a command run the instrument's `freq`
  expression.
- Commands 1-4 and the instrument vibrato are the `command` unit's own firings: the lifter writes
  the commands' modulations as `!freq`, and the instrument vibrato as the instrument's `freq`,
  its delay a term in `φ` (`(φ >= 38)` for a delay of 40), when it starts with the note; a
  vibrato that resumes after a command, or after a row that selects another instrument without a
  note, is `!freq` on each of its steps. An instrument with a vibrato delay of 0 has no vibrato.
- A wavetable row's `freq` is one firing: the step ($F1-$F4) the row runs, with its speed and the
  voice's state at the run resolved (a realtime speed reads the note the frequency base came
  from, a term of `N`). It is read as a portamento step of speed 0, so the step sets nothing but
  the frequency.

## Not covered

The lifter fails, naming what it meets:

- player builds with SIMPLEPULSE pulse tables, full ghosting, init-routine SID writes, or only
  the instrument vibrato;
- an orderlist entry that is not `[transpose][repeat] pattern`, a restart outside the
  orderlist, a loop pass that plays a different transpose, a missing or empty pattern, and a
  note byte that is no note, key-off, key-on or rest;
- a hard restart on gate timer 0 (one that turns the gate off or sets a first wave);
- wavetable $F0, $F8 and $FE, on which the playroutine stops;
- a wavetable $F7 whose pause the song hears, and a waveform value from $E0, which no waveform
  row sets;
- a wavetable $FD $10-$FF whose pause the song hears, and a pattern $D10-$DFF whose rewrite the
  song hears;
- a wavetable step row whose runs need different expressions (a vibrato step met on different
  phases), or whose other effects (a vibrato's phase, a toneporta's arrival) the song hears;
- a modulation start whose expression is not proved, or expressions that do not play the native
  register grid (see [modfit.md](../modulation/modfit.md)).
