# GoatTracker 2 (`goattracker2`)

The GoatTracker 2 playroutine (`player.s`, buffered writes) of
[`goattracker2.yaml`](../../src/unitracker/playroutines/goattracker2.yaml). The format's
canonical flush order (see [Ticks, the ghost and the canonical flush](schema.md#ticks-the-ghost-and-the-canonical-flush))
is this player's buffered order (`BUFFEREDWRITES`: the filter step, then `mt_loadregs`).

## Lifting

| | |
|---|---|
| tune | a packed GoatTracker 2 `.sid`, decompiled by pygoattracker |
| lifted from | the decompiled song and the build's constant `adparam` |
| tempo | the build's default tempo byte `d` plus 1 (when the build carries none, `d` is instrument 63's `ad` minus 1 if that `ad` is 2 or more and the instrument has no wavetable, else 5); `d` 0 is the swing `[9, 6]`, on the player's initial funk pair, and `d` 1 is not covered |
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

The speed table has no written form: its vibrato and slide speeds are terms of the `freq`
expressions, and `!tempo` and `!vtempo` write a swing's row inline.

## Events

`.i3` names the instrument. `!legato` is read from either slot and the other command from the
other; otherwise the slots are only an order.

## Commands

| command | argument |
|---|---|
| `!run` | `ctrl` name, `pw` name, `filter` name, `none` |
| `!pw` | no argument, `none` |
| `!filter` | no argument, `none` |
| `!ad` | 0-255 |
| `!sr` | 0-255 |
| `!ctrl` | 0-255 |
| `!resfilt` | 0-15/0-15 |
| `!vol` | 0-15 |
| `!cut` | 0-2040 step 8 |
| `!tempo` | 3-127, 2-129/2-129 |
| `!vtempo` | 3-127, 2-129/2-129 |
| `!freq` | `x` name, `none` |
| `!mark` | 16-255 |
| `!legato` | no argument |

Every other command is refused by name.

- `!tempo:n` and `!vtempo:n` are command $F; it plays a tempo of 2 as 3, so they start at 3.
- `!tempo:a/b` is command $E, its argument the speed-table row's two bytes, 2-129 ticks each. `!vtempo:a/b` is playroutine command 18: the player keeps one swing
  pair, and command $F's $80/$81 swing one voice on it, so 18 sets the pair, as every voice that
  swings plays it, and swings this voice. The lifter writes $E00 and $F00/$F01/$F80/$F81, which
  swing on the pair already set, as the commands that name the pair they meet ($x1 reversed,
  starting on its second tempo), once a replay proves the song unchanged; a command that meets
  pairs no one of which plays the song is not covered.
- `!vol` is command $D below $10; from $10 it is `!mark`.
- `!run:none`, `!pw:none` and `!filter:none` are commands 8, 9 and 10 with argument 0, which stop
  the control, pulse and filter programs.
- `!legato` is command 17: the lifter writes a native toneporta on a note as `!legato` plus
  the toneporta, so the note does not retrigger.
- `!freq` is command 16, which plays every expression.

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
| $FD $10-$FF | the empty row `{}`, when a replay proves its pause unheard (`{vol: 16}` is refused) |

A control mask (`ctrl: ^n`) is refused.

## Filter

| keys | set by |
|---|---|
| `res`, `route` (a mask) | filter parameter rows, which set both; `!resfilt` |
| `mode` | filter parameter rows, bits 4-6 |
| `cut` | the filter program's `cut` expression; a parameter row carries the following cutoff row's value, because the player reads both in one tick; `!cut` |
| volume | `!vol` |

`res` and `route` are one byte, which a filter parameter row, `!resfilt:r/m` (command $B) and a
wavetable $FB row set whole; from the command or the wavetable row, the byte 0 also stops the
filter program (`!resfilt:0/0`, `{res: 0, route: 0, filter: none}`). A pattern command or wavetable row that sets the cutoff, `res`/`route`
or the volume ($B-$D, $FB-$FD) sounds on the following tick: the filter step writes
`$D416`-`$D418` before the channels run, so the playroutine latches them there (`fcutreg`,
`fctrlreg`, `fmodereg`).

## Hard restart

| lookahead row | first-tick row |
|---|---|
| exactly one: `hold` 1-63 (the gate timer); `gate: 0` or nothing; with `gate: 0`, `ad`/`sr` equal to the `adparam` constant or neither | `{ctrl: n}` (n $01-$FD, the first wave, which sets the gate on), `{gate: g}`, or `{}` |

The player reads the voice's next step its instrument's gate timer ahead of each note; an
instrument without a restart reads it on the note's first tick. A note keeps the gate as the
restart left it, so its first-tick row sets it.

## Lifetime

`lifetime` declares no `flag`: a note starts the instrument's `pw` and `filter` programs, and
wavetable `run` rows ($F9, $FA) start them where the composer put them. The reader accepts
`!pw` and `!filter` on a note whose instrument starts no such program, through its fields
or its programs' rows, and whose event runs none (`!run`); the writer leaves them out.

## Modulators

`F(k)` is `ftab[k]`; `N` is `note`.

| unit | key | cell | value `v` | fires | starts | written |
|---|---|---|---|---|---|---|
| `command` | `freq` | `freq` | the offset from the frequency of the note (with the wavetable's arpeggio) | every tick of a step but tick 0, except ticks a wavetable row plays a note or runs a command | the step's command or argument changes, a note starts, or a wavetable row plays a note | `!freq:x` (command 16); without a command, the instrument's `freq` |
| `wave` | `freq` | `freq` | as `command` | once, on the tick its wavetable row runs | every run of a wavetable step row | the row's `freq` |
| `pulse` | `pw` | `pulse` | the cell | each pulse-program tick: not on the tick the voice reads its next step, nor tick 0 of a step that starts a phrase | an instrument, `!run:pw` or a wavetable $F9 sets the program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `filtercutoff`, global | the 11-bit cutoff, `filtercutoff << 3` | each filter-program tick | an instrument, `!run:f`, `!resfilt:0/0` or a wavetable $FA/$FB sets the program | the program's first row (instrument `filter`) |

- A `!freq` lasts its step. The same `!freq` on the next step continues the occurrence (`φ`
  keeps counting); a different one, or a step with another or no command, starts the next. A step
  without a command runs the instrument's `freq` expression, which a note's steps without a
  command continue.
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
- a wavetable $FD $10-$FF whose pause the song hears;
- a wavetable step row whose runs need different expressions (a vibrato step met on different
  phases), or whose other effects (a vibrato's phase, a toneporta's arrival) the song hears;
- a modulation start whose expression is not proved, or expressions that do not play the native
  register grid (see [modfit.md](../modulation/modfit.md)).
