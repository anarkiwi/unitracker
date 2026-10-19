# Shared vocabulary

The lifters decode every native row and command into one vocabulary, in chip units: the row keys
and commands of the [generic document](document.md), whose meaning is unitracker-player's
[semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md). No key or
command is one player's own.

| row key | meaning |
|---|---|
| `ctrl` | the control register's waveform and mode bits; its bit 0 lets the gate through, so the register's bit 0 is `ctrl`'s bit 0 and `gate`; `^n` an exclusive-or mask over the register's other bits, which holds until a row with a mask changes it, `v^n` both (see [The gate](#the-gate)) |
| `gate` | the gate, 0 off or 1 on (see [The gate](#the-gate)) |
| `note` | `+n` / `-n` relative to the event note, or a note name (`G#5`) for an absolute note; omitted: no change |
| `wait` | ticks before the row applies |
| `hold` | ticks the row lasts after applying; omitted: 1 |
| `ad`, `sr` | envelope registers |
| `pw` | the 12-bit pulse width, a value or an expression; `none` stops the voice's pulse program |
| `cut` | the 11-bit cutoff over `$D415`/`$D416`, a value or an expression |
| `freq` | the voice frequency's expression; an integer is a constant offset from the note's frequency |
| `res` | the resonance, bits 4-7 of `$D417` (see [Filter](#filter)) |
| `route` | the filter routing, bits 0-3 of `$D417`: a mask, or an edit for the program's voice (see [Filter](#filter)) |
| `mode` | the `$D418` filter-mode bits 4-7, volume excluded (see [Filter](#filter)) |
| `run` | the program the row starts in the slot its kind implies, by name |
| `filter` | `none`: the row stops the filter program, as `pw: none` the pulse program |
| `vol` | the volume, the low nibble of `$D418` |

A command means one thing in every player; it applies from the step that carries it.

| command | meaning |
|---|---|
| `!run:x` | start program `x` in the slot its kind implies: the control, pulse or filter program; `!run:none` stops the control program |
| `!pw:none`, `!filter:none` | stop the voice's pulse / filter program |
| `!pw`, `!filter` (no argument) | the note does not restart its instrument's pulse / filter program (see [Lifetime](#lifetime)) |
| `!ad:n`, `!sr:n` | set the envelope register to the byte |
| `!ctrl:n` | set the control register's waveform and mode bits, as a row's `ctrl` |
| `!resfilt:r/m` | set `res` and `route` (a mask), as a row's keys of those names |
| `!vol:n` | set the volume, the low nibble of `$D418` |
| `!cut:n` | set the 11-bit cutoff |
| `!tempo:n`, `!tempo:a/b` | set every voice's tempo: `n` ticks per step, or a swing whose steps alternate `a` and `b` ticks |
| `!vtempo:n`, `!vtempo:a/b` | set this voice's tempo |
| `!freq:x` | drive the voice frequency by expression `x` until a note starts the instrument or another `!freq` (see [Lifetime of `!freq`](#lifetime-of-freq) and [Modulators](../modulation/expressions.md)) |
| `!freq:none` | return the voice frequency to its instrument's `freq` |
| `!legato` | the event's note does not retrigger; it needs a note (see [Legato](../modulation/expressions.md#legato)) |

A playroutine's `commands` and `lifetime` declare the native commands its lifter decodes into
these; each playroutine's file ([goattracker2](../playroutines/goattracker2.md#commands),
[sidwizard](../playroutines/sidwizard.md#commands), [defmon](../playroutines/defmon.md#commands))
lists them. A shared key means the same wherever it appears: in program rows and in instruments,
where `ctrl` names the program run on the control register, `pw` and `filter` the pulse and
filter programs, and `restart` the hard restart.

## The gate

`ctrl` and `gate` each hold until a row sets them; a note, `off` and `on` set the gate as the
player does. A row's `ctrl` leaves the gate as it is, but on a restart's first-tick row, where a
`ctrl` sets the gate on. A playroutine whose rows take a mask says which rows change it.

## Chords

A chord or arpeggio is a `ctrl` program of relative-note rows:
`[{ctrl: 65, note: +0}, {wait: 1, note: +5}, {wait: 1, note: +8}, {loop: 1}]`. A chord that loops
is the program's looping tail; one that ends runs into the program's next row. An event that
plays a different chord starts that program with `!run`.

## Filter

`$D417` is `res` (bits 4-7) over `route` (bits 0-2 voices 1-3, bit 3 the external input).
`$D418` is `mode` (bit 4 low-pass, 5 band-pass, 6 high-pass, 7 voice 3 off) over the volume
(bits 0-3, `!vol`). A key a row leaves out is unchanged.

`route` is the mask, or an edit for the voice that runs the program: `route: + m` adds that
voice and the bits of `m`, `route: - m` removes them, and `m` is left out when it is 0
(`route: +`, `route: '-'`). The space belongs to the form: `route: +8` is the mask 8.

## Hard restart

A `restart` is a program (prefix `r`) anchored to a note: its last row applies on the note's
first tick, and each earlier row its `hold` ticks before the row after it. A row uses `hold`,
`ctrl`, `gate`, `ad` and `sr`; a player may give a lookahead row's `ctrl` a further effect, which
its file states where it defines the row (SID-Wizard's `ctrl: 24` also pauses the voice's
tables). A note's restart is its instrument's (`restart: r1`) in every player:

```yaml
restart:
  r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
```

Four ticks before the note the gate goes off with envelope $0F00; on its first tick the control
register takes $09 with the gate on. A note with `!legato` runs no restart, and an instrument
without `restart` has none. A row's place in its program is part of its shape (see
[Codecs](../playroutines/schema.md#codecs)).

A player whose notes arm the restart on the steps before them declares it with its [instrument
columns](#instrument-columns), `event: {..., restart: c}`: the restart of a note's instrument is
native command `c` on the step its lookahead rows' holds put before the note, running those rows
as a program of the command's record type.

## Lifetime

Whether a note restarts its instrument's pulse or filter program is a property of the note:
`!pw` and `!filter` mean it does not, as `!legato` means it does not retrigger. A playroutine
that keeps the program by an instrument bit declares
`lifetime: {pw: {flag: {flags: 0x40}}, filter: {...}}`: `flag` is the instrument field and bit
mask, and `field` the instrument key of the program (default: the slot's name). A note that
names no instrument plays the voice's last one. A legato note restarts nothing.

## Lifetime of `!freq`

A `!freq:x` lasts until a note starts the instrument (a legato note does not) or another `!freq`
replaces it; steps between, empty or with other commands, continue it. `!freq:none` returns the
frequency to the instrument's `freq` before a note does.

A playroutine whose native `!freq` ends on a step that carries no command declares `lasts: step`
on its `freq` entry: there each step without a command after a `!freq` is `!freq:none`.

## Running values

A command that sets what the voice already has changes nothing (`!tempo:16` after the phrase's
`!tempo:16`, `C4.i1!ad:1` where `i1` has `ad: 1`). A note that starts an instrument sets the
instrument's `ad` and `sr`, and its `freq`. A program command restarts its program and a swing
its alternation, so only stopping a program (`none`) can change nothing of theirs.

## Envelope commands

`!ad:n` and `!sr:n` are the byte the envelope register takes. A player whose envelope commands set
one nibble over the instrument's byte (an envelope command entry, see
[Codecs](../playroutines/schema.md#codecs)) has them decoded as the byte they write, from the
voice's instrument. A phrase whose bytes differ between the places it plays becomes one phrase
per place.

## Instrument columns

A playroutine whose events arm programs directly, with no native instrument, declares
`instrument: {type: t, base: b, event: {type: ins, column: k, commands: {c: f}}}`: an event's
instrument column (record `t` at index value - `b`) is field `k` of an `ins` record and the
argument of its command `c` field `f`. The lifter names one instrument per combination of
programs the events arm, and the event names that instrument.
