# Shared vocabulary

Program rows and commands use one vocabulary in every song, in chip units. No key, command or
record type is one player's own.

| row key | meaning |
|---|---|
| `ctrl` | the control register's waveform and mode bits; its bit 0 lets the gate through, so the register's bit 0 is `ctrl`'s bit 0 and `gate`; `^n` an exclusive-or mask over the register's other bits, `v^n` both (see [The gate](#the-gate)) |
| `gate` | the gate, 0 off or 1 on (see [The gate](#the-gate)) |
| `note` | `+n` / `-n` relative to the event note, or a note name (`G#5`) for an absolute note; omitted: no change |
| `wait` | ticks before the row applies (see [Timing](#timing)) |
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

A command means one thing in every player; it applies on the step that carries it.

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
| `!tempo:n`, `!tempo:a/b` | set every voice's tempo, as the document's `tempo`: `n` ticks per step, or a swing whose steps alternate `a` and `b` ticks |
| `!vtempo:n`, `!vtempo:a/b` | set this voice's tempo |
| `!freq:x` | drive the voice frequency by expression `x` (see [Modulators](../modulation/expressions.md)) |
| `!mark:n` | a mark that changes no sound: the player reads the byte and plays nothing |
| `!restart:r` | play hard restart `r` before the note (see [Hard restart](#hard-restart)) |
| `!legato` | the event's note does not retrigger (see [Legato](../modulation/expressions.md#legato)) |

A playroutine takes the commands its `commands`, `restart` and `lifetime` declare, over the
arguments its codec accepts; each playroutine's file
([goattracker2](../playroutines/goattracker2.md#commands),
[sidwizard](../playroutines/sidwizard.md#commands), [defmon](../playroutines/defmon.md#commands))
lists them. A shared key means the same wherever it appears: in program rows, in fixed records, and
in instruments, where `ctrl` names the program run on the control register, `pw` and `filter` the
pulse and filter programs, and `restart` the hard restart. A row or command a player can't express
is rejected on reading, naming the key or command and the player (`defmon: the player can't express
!tempo:6`); the writer refuses to write a value that would not read back. A key, command or record
type outside the playroutine's codec shapes, record types and the shared command names is refused
with one message naming it and where it appeared (`unknown key k in pw`, `unknown command !mod in
'-!mod:1'`, `unknown record type 'speedtab'`, `unknown key registers in document`).

## Timing

A row applies after its `wait` and lasts its `hold`. A `wait: n` row is `hold: +n` on the row
before it, except on a program's entry row, which has no row before it, and on the row a loop
returns to, which two rows precede; so both keys stay.

## The gate

`ctrl` and `gate` each hold until a row sets them; a note, `off` and `on` set the gate as the
player does. A row's `ctrl` leaves the gate as it is, but on a restart's first-tick row, where a
`ctrl` sets the gate on: the row is written without `gate: 1`, and `gate: 0` there is refused.
The mask `^n` holds until a row sets `gate` or another mask; a row that sets `gate` without a mask
sets it to 0.

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

A `restart` is a program (record type `restart`, prefix `r`) anchored to a note: its last row
applies on the note's first tick, and each earlier row its `hold` ticks before the row after it.
A row uses `hold`, `ctrl`, `gate`, `ad` and `sr`. An instrument names its restart
(`restart: r1`); in a playroutine whose instruments have none, a note names its own
(`C3.i2!restart:r1`):

```yaml
restart:
  r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
```

Four ticks before the note the gate goes off with envelope $0F00; on its first tick the control
register takes $09 with the gate on. A note with `!legato` runs no restart, and an instrument
without `restart` has none. A playroutine plays the shapes its codec accepts; a row's place in
its program is part of its shape (see [Codecs](../playroutines/schema.md#codecs)).

A playroutine declares event restarts with `restart: {command: c}`: a note's `!restart:r` is
read as command `c` on the step its lookahead rows' holds put before the note, running those rows
as a program of the command's record type, and that step must be empty. The writer folds such a
command back onto its note when it is alone on its step, followed only by empty steps up to a
note with a free command slot, and its program ends and fits in the steps.

## Lifetime

Whether a note restarts its instrument's pulse or filter program is a property of the note:
`!pw` and `!filter` mean it does not, as `!legato` means it does not retrigger. They
need a note and refuse one that itself starts the program. A note that names no instrument plays
the voice's last one; the reader finds it by walking each voice's plays twice through its loop.
The playroutine declares the commands it plays as
`lifetime: {pw: {flag: {flags: 0x40}}, filter: {...}}`, or `lifetime: [pw, filter]` when no entry
has a key; an entry's `field` is the instrument key of the program (default: the slot's name):

| declaration | the command |
|---|---|
| with `flag` (an instrument field and bit mask) | the bit, which keeps the program on a note that names no instrument: the writer puts the command on every such note of an instrument with the bit set, and the reader sets the bit from those notes; it refuses an instrument some of whose such notes carry it and others not, and the command on a note that names an instrument |
| without `flag` | holds on a note whose instrument starts no such program; the reader refuses it on a note whose instrument starts one |

A note played under instruments of which some keep a program and others restart it has no
written form. A legato note restarts nothing, so the command adds nothing there.

## Envelope commands

A command entry `{name: ad, nibble: s, column: c}` sets one nibble of an envelope register over
the instrument's byte. The document writes each as the byte it writes, `!ad:n` or `!sr:n` in the
slot of its column, from the voice's instrument (found by walking each voice's plays twice
through its loop); the reader plays each byte as the nibble command that changes one nibble of
the instrument's byte. A phrase whose bytes differ between the places it plays becomes one phrase
per place.

## Instrument columns

A playroutine whose events arm programs directly, with no native instrument, declares
`instrument: {type: t, base: b, event: {type: ins, column: k, commands: {c: f}}}`: an event's
instrument column (record `t` at index value - `b`) is field `k` of an `ins` record and the
argument of its command `c` field `f`. The writer names one instrument per combination of
programs the events arm, and the event names that instrument.
