# Shared vocabulary

Program rows and commands use one vocabulary in every song, in chip units. No key, command or
record type is one player's own.

| row key | meaning |
|---|---|
| `ctrl` | control register value; `^n` sets an exclusive-or mask the voice's control output keeps until a row sets another, `v^n` both |
| `gate` | the gate the control register is written with until a row, note or `off`/`on` sets it: 0 off, 1 on |
| `note` | `+n` / `-n` relative to the event note, `=n` absolute (format numbering, C2 = 24); omitted: no change |
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

| command | meaning |
|---|---|
| `!run:@x` | start program `x` in the slot its kind implies; `!run:none` stops it |
| `!ad:`, `!sr:`, `!ctrl:` | set the register to the byte |
| `!resfilt:n` | set `$D417` whole, `n = 16*res + route` |
| `!vol:n` | set the volume, the low nibble of `$D418` |
| `!cut:n` | set the 11-bit cutoff |
| `!tempo:n`, `!vtempo:n` | n ticks per step, for all voices / this voice, from the step that carries the command |
| `!funk:a/b`, `!funk:none` | swing tempo, for all voices: the steps alternate `a` and `b` ticks; `none` swings on the pair already set |
| `!freq:@x` | drive the voice frequency by expression `x` (see [Modulators](../modulation/expressions.md)) |
| `!legato:0` | the event's note does not retrigger (see [Legato](../modulation/expressions.md#legato)) |
| `!mark:n` | a timing mark that changes no sound |
| `!restart:@r` | the note's hard restart, for a player whose instruments have none (see [Hard restart](#hard-restart)) |
| `!pulse:0`, `!filter:0` | the note does not restart the instrument's pulse / filter program (see [Lifetime](#lifetime)) |

A playroutine takes the commands its `commands`, `restart` and `lifetime` declare, over the
arguments its codec accepts; each playroutine's file lists them. A shared key means the same
wherever it appears: in program rows, in fixed records, and in instruments, where `ctrl` names the
program run on the control register, `pw` and `filter` the pulse and filter programs, and `restart`
the hard restart. A row or command a player can't express is rejected on reading, naming the key or
command and the player (`defmon: the player can't express !tempo:6`); the writer refuses to write a
value that would not read back. A key, command or record type outside the playroutine's codec
shapes, record types and the shared command names is refused with one message naming it and where it
appeared (`unknown key k in pw`, `unknown command !mod in '-!mod:1'`, `unknown record type
'speedtab'`, `unknown key registers in document`).

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
(`C3.i2!restart:@r1`):

```yaml
restart:
  r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]
```

Four ticks before the note the gate goes off with envelope $0F00; on its first tick the control
register takes $09 with the gate on. A note with `!legato:0` runs no restart, and an instrument
without `restart` has none. A playroutine plays the shapes its codec accepts; a row's place in
its program is part of its shape (see [Codecs](../playroutines/schema.md#codecs)).

A playroutine declares event restarts with `restart: {command: c}`: a note's `!restart:@r` is
read as command `c` on the step its lookahead rows' holds put before the note, running those rows
as a program of the command's record type, and that step must be empty. The writer folds such a
command back onto its note when it is alone on its step, followed only by empty steps up to a
note with a free command slot, and its program ends and fits in the steps.

## Lifetime

Whether a note restarts its instrument's pulse or filter program is a property of the note:
`!pulse:0` and `!filter:0` mean it does not, as `!legato:0` means it does not retrigger. They
need a note and refuse one that itself starts the program. A note that names no instrument plays
the voice's last one; the reader finds it by walking each voice's plays twice through its loop.
The playroutine declares the commands it plays as
`lifetime: {pulse: {field: pw, flag: {flags: 0x40}}, filter: {...}}`, `field` the instrument key
of the program:

| declaration | the command |
|---|---|
| with `flag` (an instrument field and bit mask) | the bit, which keeps the program on a note that names no instrument: the writer puts the command on every such note of an instrument with the bit set, and the reader sets the bit from those notes; it refuses an instrument some of whose such notes carry it and others not, and the command on a note that names an instrument |
| without `flag` | holds on a note whose instrument starts no such program; the reader refuses it on a note whose instrument starts one |

A note played under instruments of which some keep a program and others restart it has no
written form. A legato note restarts nothing, so the command adds nothing there.

## Envelope commands

A command entry `{name: ad, nibble: s, slot: k}` sets one nibble of an envelope register over
the instrument's byte. The document writes each as the byte it writes, `!ad:n` or `!sr:n` in the
slot of its column, from the voice's instrument (found by walking each voice's plays twice
through its loop); the reader plays each byte as the nibble command that changes one nibble of
the instrument's byte. A phrase whose bytes differ between the places it plays becomes one phrase
per place.

## Instrument columns

A playroutine whose events arm programs directly, with no native instrument, declares
`instrument: {type: t, base: b, writes_as: {type: ins, column: k, commands: {c: f}}}`: an event's
instrument column (record `t` at index value - `b`) is field `k` of an `ins` record and the
argument of its command `c` field `f`. The writer names one instrument per combination of
programs the events arm, and the event names that instrument.
