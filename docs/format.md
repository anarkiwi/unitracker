# unitracker song format (v1.19)

A unitracker song is one YAML document, played by the generic player (`unitracker-play`) on an
emulated SID. Any prefix of the document is a playable partial song: a short header, then one
timeline in which every instrument, program, expression and phrase is defined in the row where it
is first used. Everything a player does is in its **playroutine**, which the song references
from the library in `src/unitracker/playroutines/`; what each library playroutine accepts is in
[playroutines/](playroutines/).

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.19
playroutine: {ref: goattracker2, constants: {deftempo: 11}}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
hints: {ticks_per_step: 16, phrase_steps: 48}
song:
  - def:
      restart:
        r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]
      ins:
        i1: {ad: 1, sr: 136, ctrl: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: =68},
            {ctrl: 65, note: +0}, end], pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: [{cut: 1024, res: 12,
              route: 1, mode: 16}, end], restart: r1}
        ...
      ctrl:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0}, {wait: 1,
            note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!run:@c1*3, off*3, G4!run:@c1*3, ...]
        p3: [off!tempo:16*48]
    play: [p1, p2, p3]
  - def:
      ...
      x:
        x1: (v + 32) % 65536
        x2: v - v*[F(N) != (v + F(N)) % 65536] + (-[F(N) > (v + F(N) - 32) % 65536]*((v - 32) % 65536)
          + (v - 32) % 65536)*[F(N) < (v + F(N)) % 65536] + [F(N) >= (v + F(N) + 32) % 65536]*[F(N) >
          (v + F(N)) % 65536]*((v + 32) % 65536)
        x3: (v + 64*[(φ - 3) % 12 >= 6] - 32) % 65536
      phrase:
        ...
        p6: [off*3, A#3.i4!freq:@x1, C4!legato:0!freq:@x2, D4, D#4*2, C4, G4*2, C5, G#4!freq:@x1, ...]
    play: [p4, p5, p6]
loop: 0
```

## Document

Top-level keys, in this order:

| key | content |
|---|---|
| `unitracker` | `1.19`; readers reject any other version |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (see Embedding) |
| `meta` | `title`, `author`, `released` |
| `hints` | reading aids the player ignores, each a positive integer and both optional: `ticks_per_step`, the ticks per step as the lifter derived it, and `phrase_steps`, the common phrase length in steps when every phrase has one (the writer derives it and refuses a stated value that disagrees); readers reject any other key |
| `clock`, `voices` | only where they differ from the playroutine's `defaults` |
| `song` | the timeline: a list of rows |
| `loop` | the row the song loops to, or `~` to end |

`playroutine.constants` lists only the constants that differ from the library's.
`playroutine.tables` patches the library's tables: `{index: value}` for the entries that
differ, or a full list when the length differs.

The patch holds only entries the song can read. The writer plays the song, recording every
table index read, until its complete state (all cells, cursors and registers) recurs. Playback
is deterministic, so no later tick reads anything new, and an entry never read cannot change the
output; the library default stands in for it. If the state does not recur within a bound, the
patch is written whole.

### Embedding

`unitracker-lift --embed` writes the playroutine in full instead of `ref`, with its `tables`
complete, so the file plays without the library.

## Timeline

Each row is `{def: {...}, play: [entry per voice]}`; `def` is present only when something is
first used in that row. An entry is:

| entry | meaning |
|---|---|
| `p3` | the voice starts phrase `p3` |
| `p3+5`, `p3-2` | the voice starts phrase `p3` transposed by that many semitones |
| `end` | the voice stops |
| `~` | the voice is still playing its phrase |

A row starts at the earliest end of the voices' current phrases. Every voice whose phrase ends
there must have an entry, and every other voice must be `~`; readers reject anything else.
Repeats are written out as rows. When the voices' loops have different lengths, the rows run
until all of them are back in the same state together, and `loop` is the row where that state
first occurred.

## Definitions

`def` maps a record type, `x`, or `phrase` to `{name: value}`. Names are the record type's
prefix and a number, given in first-use order (`i1`, `i2`, ..., `c1`, ...; expressions `x1`,
...; phrases `p1`, ...). The prefixes are distinct, so a name says what it is. The reader
allocates each type's indices in definition order from the type's `first` index, so a name
stands for whatever index the playroutine needs.

Every playroutine writes its records under these record types (its `record_types` entry names
the type each native table is written as, `writes_as`):

| record type | prefix | holds |
|---|---|---|
| `ins` | `i` | an instrument |
| `ctrl` | `c` | a control program: waveform, notes, and any other key a row of the player can set |
| `pw` | `pw` | a pulse program |
| `filter` | `f` | a filter program |
| `restart` | `r` | a hard restart (see Hard restart) |
| `x` | `x` | a modulator expression (see Modulators) |

- **Rows** use the shared row keys (see Shared vocabulary) wherever the playroutine's codec can
  express them: `{ctrl: 129, note: =68}`. A row no codec shape covers is written in the type's
  `form`: a `list` row holds the fields in declaration order, with trailing defaults left out
  (`[4, 32]`); a `map` row holds the fields that differ from their defaults.
- **Programs** are the rows of a chained type, in the order they run, followed by `end` or
  `{loop: k}` (k is a row of the same program). A program is cut from the player's chained
  tables at every entry point. A tail two programs share is written in both, which is exact
  because rows are deterministic. A program that is one expression is written as that
  expression: `pw: 16*abs((φ - 32) % 128 - 64) + 512`.
- **References.** A field that references another record holds a name. A program that only one
  instrument uses is written inline in that instrument instead
  (`ctrl: [{ctrl: 9, note: +0}, {ctrl: 65, note: +0}, end]`).
- **Expressions** (`x`) are the modulator expressions an event command names (`!freq:@x1`):
  `x: {x1: (v + 32) % 65536}`. An expression no command names is written where it is used.
- **Fixed** record types (`fixed`, such as an initial voice state) keep their indices and are
  defined in the first row: `vinit: {0: {note: =36, pw: 256, freq: 2227}, ...}`. They use the
  shared keys with their row meaning wherever one fits, and an omitted field is 0.

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command:arg[!command:arg]][*steps]
```

- `note` is a note name such as `C2`, `A#3` or `C10`, `-` for no note, `off` (gate off) or `on`
  (gate on). Names are real pitch: `C2` is 65.4 Hz in every song, whatever index the player's
  frequency table gives it (`note_offset`).
- `.i3` names the instrument.
- `!run:@c1` is a named command with an argument: a number, `@name` for a command that
  references a record or an expression, `none`, or values joined by `/` for a command of several
  values (`!funk:6/7`). A command's argument is a byte: `!freq:@x` names one of the first 255
  expressions. An event carries at most two commands, its two slots, then `!pulse:0` and
  `!filter:0` (see Lifetime), which take no slot. The first slot is the instrument column and
  the second the FX column; an event that names an instrument fills the instrument column with
  it, so its commands are the FX column's. A playroutine whose column holds one command declares
  it (`exclusive`), and an event with two such commands is refused, naming both.
- `*3` makes the event last 3 steps; the steps after the first are empty.

## Shared vocabulary

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
| `res` | the resonance, bits 4-7 of `$D417` (see Filter) |
| `route` | the filter routing, bits 0-3 of `$D417`: a mask, or an edit for the program's voice (see Filter) |
| `mode` | the `$D418` filter-mode bits 4-7, volume excluded (see Filter) |
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
| `!freq:@x` | drive the voice frequency by expression `x` (see Modulators) |
| `!legato:0` | the event's note does not retrigger (see Legato) |
| `!mark:n` | a timing mark that changes no sound |
| `!restart:@r` | the note's hard restart, for a player whose instruments have none (see Hard restart) |
| `!pulse:0`, `!filter:0` | the note does not restart the instrument's pulse / filter program (see Lifetime) |

A playroutine takes the commands its `commands`, `restart` and `lifetime` declare, over the
arguments its codec accepts; each playroutine's file lists them. A shared key means the same
wherever it appears: in program rows, in fixed records, and in instruments, where `ctrl` names
the program run on the control register, `pw` and `filter` the pulse and filter programs, and
`restart` the hard restart. A row or command a player can't express is rejected on reading,
naming the key or command and the player (`defmon: the player can't express !tempo:6`); the
writer refuses to write a value that would not read back.

### Chords

A chord or arpeggio is a `ctrl` program of relative-note rows:
`[{ctrl: 65, note: +0}, {wait: 1, note: +5}, {wait: 1, note: +8}, {loop: 1}]`. A chord that loops
is the program's looping tail; one that ends runs into the program's next row. An event that
plays a different chord starts that program with `!run`.

### Filter

`$D417` is `res` (bits 4-7) over `route` (bits 0-2 voices 1-3, bit 3 the external input).
`$D418` is `mode` (bit 4 low-pass, 5 band-pass, 6 high-pass, 7 voice 3 off) over the volume
(bits 0-3, `!vol`). A key a row leaves out is unchanged.

`route` is the mask, or an edit for the voice that runs the program: `route: + m` adds that
voice and the bits of `m`, `route: - m` removes them, and `m` is left out when it is 0
(`route: +`, `route: '-'`). The space belongs to the form: `route: +8` is the mask 8.

### Hard restart

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
its program is part of its shape (see Codecs).

A playroutine declares event restarts with `restart: {command: c}`: a note's `!restart:@r` is
read as command `c` on the step its lookahead rows' holds put before the note, running those rows
as a program of the command's record type, and that step must be empty. The writer folds such a
command back onto its note when it is alone on its step, followed only by empty steps up to a
note with a free command slot, and its program ends and fits in the steps.

### Lifetime

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

### Envelope commands

A command entry `{name: ad, nibble: s, slot: k}` sets one nibble of an envelope register over
the instrument's byte. The document writes each as the byte it writes, `!ad:n` or `!sr:n` in the
slot of its column, from the voice's instrument (found by walking each voice's plays twice
through its loop); the reader plays each byte as the nibble command that changes one nibble of
the instrument's byte. A phrase whose bytes differ between the places it plays becomes one phrase
per place.

### Instrument columns

A playroutine whose events arm programs directly, with no native instrument, declares
`instrument: {type: t, base: b, writes_as: {type: ins, column: k, commands: {c: f}}}`: an event's
instrument column (record `t` at index value - `b`) is field `k` of an `ins` record and the
argument of its command `c` field `f`. The writer names one instrument per combination of
programs the events arm, and the event names that instrument.

## Modulators

A modulator drives one cell of the player (a voice's frequency, its pulse width, or the filter
cutoff) by an integer expression evaluated at each of the unit's firings.

### Grammar

| form | meaning |
|---|---|
| `12` | an integer |
| `a + b`, `a - b`, `a * b`, `-a` | integer arithmetic, unbounded |
| `a // b` | floored division: rounds toward minus infinity |
| `a % b` | floored modulo: the result has the sign of `b` |
| `abs(a)`, `min(a, b)`, `max(a, b)` | |
| `[a op b]` | Iverson bracket of one comparison: 1 when it holds, else 0; `op` is `==`, `!=`, `<`, `<=`, `>` or `>=` |
| `v` | the cell's value before the firing |
| `φ` | the firings since the modulator started, from 0 |
| `N` | the voice's sounding note, format numbering (C2 = 24) |
| `F(k)` | the player's frequency word of format note `k` |
| `T[k]` | entry `k` of a table `T` the playroutine declares (`modulators: tables:`); 0 outside its size |

Anything else is rejected on reading, naming the text (`... is outside the grammar`,
`... calls no grammar function`, `[...] is not one comparison`, `unknown table 'T'`).

### Evaluation

Each firing of an active expression `f` computes, from the state before it,

```
v' = f(v, φ, N)        stored in the cell, wrapped to its width
φ' = φ + 1
```

A start opens an occurrence at the unit's next firing, with `φ = 0` and `v` the unit's value
then; it runs until the unit's next start. An expression with no `v` is a closed form in `φ`:
the value after firing `φ`. A plain integer in an expression slot is a constant expression.
Values a modulator carries from earlier playback (a slide's start frequency, a delay counter, an
accumulator's direction) are constants of its expression, which the lifter resolves at the
modulator's start. A start that selects no expression leaves the unit to the playroutine's own
firing.

### Units

A unit drives one shared key, `freq` (a voice's frequency), `pw` (a voice's pulse width) or
`cut` (the filter cutoff). Its value `v` is its `chip` map's value, else its cell's (see Modulator
declarations); each playroutine's file gives its units.

### Where expressions go

| place | written as |
|---|---|
| a program row's `pw`, `cut` or `freq` | the row key's expression |
| an instrument's `pw` or `filter` program | the program's one expression |
| an instrument's `freq` | the pitch expression a note starting the instrument runs |
| an event's `!freq:@x1` | a name of an `x` definition |

A key that also takes a plain value reads an integer as that value and a string as an
expression (`cut: '1024'` is the constant expression, written back quoted); elsewhere an integer
is a constant expression. An expression that contains a comma is quoted inside a YAML flow map:
`pw: '2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048'`.

### Legato

`!legato:0` makes the event's note sound without retriggering it: no gate-off, hard restart or
instrument start. The playroutine declares the command code it is (`modulators.legato`).

## Canonical form

The writer produces one text per song:

- names are given in first-use order, each a record type's prefix and a number;
- records and rows the song never reaches are dropped;
- identical phrases, and phrases that are equal up to a transpose, are merged, and the transpose
  moves into the play entry;
- equal expression texts share one definition;
- runs of empty steps become `*steps`;
- a playroutine's event restarts are written on their notes (see Hard restart);
- a phrase whose envelope bytes differ between the places it plays is split into one phrase per
  place (see Envelope commands), the inverse of the merge;
- no-op commands are dropped.

Writing what the reader returns gives the same bytes.

## The playroutine

The library holds one definition per player build:

```yaml
name: str
constants: {name: int}
defaults: {clock: {cycles_per_tick: 19656, clock_hz: 985248}, voices: 3}
onset: onset     # the voice cell that is 1 once the voice's registers hold its first note
tables: {name: [ints]}                    # player-build data; reads out of range give 0
note_offset: 0                            # frequency-table index of format note 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
                                          # writes_as: {type, column, commands}: see Instrument columns
restart: {command: 1}                     # event restarts, played by this command (see Hard restart)
lifetime: {pulse: {field: pw, flag: {flags: 0x40}}}   # !pulse:0 and !filter:0 (see Lifetime)
exclusive: "code < 0x100"                 # over code and instrument: commands of a one-command column
commands: {8: {name: run, ref: ctrltab}, 15: [{name: tempo, when: ..., arg: ..., raw: ...}], ...}
record_types: {name: {...}}
functions: {name: {args: [a, b], expr: "..."}}   # inlined into expressions
global: {cell: {width: 8, init: 0}}       # width omitted: unbounded
voice:  {cell: {width: 8, init: 0}}       # one copy per voice
records: {name: {index: {field: ...}}}    # playroutine-owned, e.g. command dispatch
procs: {name: [actions]}
init: [actions]                           # first tick only, global scope
tick: [actions]                           # every later tick, global scope
ghost:
  global: {0x15: expr, 0x16: expr, 0x17: expr, 0x18: expr}
  voice: {0: expr, 1: expr, ..., 6: expr} # offsets within a voice
modulators: {freq: ..., notes: [...], units: [...]}   # see Modulator declarations
```

### Record types

| key | meaning |
|---|---|
| `fields` | a list of names, or `{name: default}`; a missing field reads as its default |
| `form` | `list` (default) or `map` |
| `writes_as` | the record type a document writes it as (default: its own name; see Definitions) |
| `prefix` | the name prefix (default: the first letter of the written name); distinct from the other types' and from the phrases' `p` |
| `chain` | the field holding the next row: the type is a table of programs |
| `none` | the index that means "no row"; references equal to it are plain numbers |
| `stop` | an expression over a row's fields after which a program ends |
| `first`, `max` | the indices the reader allocates (default `none + 1`, or 0; up to 255) |
| `pin` | indices the playroutine reads directly; they are defined first |
| `fixed` | the rows keep their indices |
| `refs` | `{field: type}`, or `{field: [{type: t, when: expr}, ...]}` for a reference that depends on the row |
| `codec` | shapes writing rows as shared keys (see Codecs) |
| `end` | raw fields set on a program's last row when it is followed by `end` |
| `expr` | the field (a reference to `x`) holding a row's modulator expression; a program of one row that sets only it is written as the expression |
| `retired` | `{expression: reason}`: raw rows that have no written form; the reader and the writer refuse a row for which an expression holds, giving the reason |
| any other key | a template: an action list run for a row |

The record type `x` is the format's own: it holds the expressions, and a playroutine
cannot declare it.

A template reads the row it runs for as `row.<field>`. `run: {record: r, index: i,
field: f}` runs record type `r`'s template `f` for row `i` when that row exists.
A control-program table, for example, decodes its bytes in a template:

```yaml
ctrltab:
  writes_as: ctrl
  none: 0
  chain: next
  fields: [w, n, next]
  do:
    - if: "row.w >= 0xF0"
      then: [{set: {arg: row.n}}, {run: {record: wavecmd, index: "row.w & 0x0F", field: do}}, ...]
    ...
```

### Codecs

A codec shape has a `when` over the raw fields, the playroutine's constants, and for a
program row its place: `first` and `last` are 1 for the program's first row and for the
row before its `end`; `keys` (`{key: {value, omit, abs, expr, eor}}`:
the shared key's value from the raw fields, when to leave it out, for `note` whether
it is absolute, the raw field holding the key's expression, and for `ctrl` its mask as
`{value, omit}`), `raw` (each raw field from the written keys, with `has_<key>`,
`note_abs`, and for a mask `has_eor` and `eor` defined) and `keep` (raw fields
written under their own names; a kept reference is written as a name). A key with `expr`
is written as the expression when that field is set; a key with both `value` and `expr`
reads an integer as a value and a string as an expression. A key given as
`{ref: type, field: f}` is the reference raw field `f` holds, written as the record's name
(`run: pw1`); the reader takes the shape whose `ref` is the type the name has. A key given as
`{none: true}` is written `none` (`pw: none`) and reads only from `none`. The writer
uses the first shape whose `when` holds and refuses a row that would not read back; a `map` row no
shape fits is an error. The reader accepts a written row only if encoding its inverse
gives the row again. A command entry has a `name`, an optional `ref` (a record type, or
`x` for an expression), and for a plain argument optional `when`, `arg` (raw to written)
and `raw` (written to raw), checked the same way; `args` (`{a: expr, b: expr}`) writes the
argument as several values joined by `/`, which `raw` reads by those names
(`!funk:6/7`). A `ref` entry with `inline: true` writes
the referenced row's fields, each a byte, joined by `/` (`!funk:6/7`); the reader allocates
one row per distinct argument, and the type has no definitions of its own. An entry `{name: ad, nibble: s, slot: k}`
is an envelope command: the code sets the nibble at shift `s` of the register `name` over
the instrument's field `name`, played from event slot `k`, and is written as the byte it
writes (see Envelope commands).

A raw field given as `{search: [lo, hi]}` is the first value in that range whose row
writes back as the written one. Codec expressions may read the song's tables and the
playroutine's functions. A list row a shape covers is rejected: it has a shared-key form.

### Cells and expressions

Every assignment wraps to the target cell's `width`, using two's complement for
negative values. Expressions use Python syntax, restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`, `record[i].field` and, in a template, `row.field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`.

Arithmetic is unbounded; only assignment wraps. These are the playroutine's
expressions; a song's modulator expressions use the grammar of Modulators.

### Actions

| action | meaning |
|---|---|
| `set: {cell: expr, ...}` | Assign in order, each wrapped to its cell's width. |
| `if: expr`, `then: [...]`, `else: [...]` | Conditional. |
| `call: proc`, `with: {cell: expr}` | Assign the `with` cells, then run the proc. |
| `run: {record: r, index: expr, field: f}` | Run a record type's template, or the action list stored in a record field. |
| `for_voices: [...]` | Run the list once per voice, in voice order, with that voice's cells bound. |
| `fetch: true` | Read this voice's next step (see The cursor). |
| `return: true` | Leave the innermost proc, template, record action list, `for_voices` body, or `init`/`tick`. |
| `accumulate: cell` | The bounded accumulator. |

### The bounded accumulator

```yaml
accumulate: cell
step: expr             # signed; with toward, a magnitude
min: expr              # optional bounds
max: expr
toward: expr           # optional target: approach it from either side
signed: false          # compare as two's complement at the cell's width
clamp: true | false | reflect
on_bound: [actions]
```

```
v0 = cell
if toward given:
    if v0 == T: done
    if v0 < T: s = step, max = T  else: s = -step, min = T
else: s = step
v  = wrap(v0 + s, width(cell))           # bounds are tested after wrapping, as players do
hit = v > max (if max given) or v < min (if min given)   # signed: compared as two's complement
if hit and clamp is true: v = the bound crossed
if hit and clamp is reflect: v = wrap(v0 - s, width(cell))
cell = v
if hit: run on_bound
```

With `signed`, `v0`, `v`, `min`, `max` and `T` are read as two's complement at the cell's
width (`x - 2**w` when `x >= 2**(w-1)`), including the `toward` comparisons `v0 == T` and
`v0 < T`; the stored value still wraps to the width. `signed` needs a cell with a width, and
`clamp: reflect` cannot be combined with `toward`. A reflecting direction is an ordinary
cell inside `step`, flipped in `on_bound`:

```yaml
accumulate: freq
step: "vspd if vd == 0 else 0 - vspd"
min: lo
max: hi
clamp: reflect
on_bound: [{set: {vd: "vd ^ 1"}}]
```

### Modulator declarations

`modulators` declares the player's units and what their expressions read.

| key | meaning |
|---|---|
| `freq` | the word `F(k)` reads at index `k + note_offset`: `{word: table}`, or `{hi: table, lo: table}` joined as `hi << 8 \| lo` |
| `notes` | the voice cells that hold the note; `N` is the first, less `note_offset` |
| `transpose` | a voice cell added to the note for `N` |
| `legato` | `{cell, command}`: the cell set while a note does not retrigger, and the command code written as `!legato:0` |
| `tables` | `{name: {entry: expr, size: n}}`: the tables `name[k]` expressions read; `entry` reads `i` and the playroutine's tables |
| `functions` | `{name: expr}`: playroutine functions rewritten over the named tables, for analysis |
| `units` | the units, below |

| unit key | meaning |
|---|---|
| `name` | the unit's name |
| `key` | the shared key it drives: `freq`, `pw` or `cut` |
| `cell` | the driven cell; a pair `hi:lo[:k]` is the value `hi << k \| lo & (2**k - 1)` (k 8 when omitted) |
| `chip` | `{width, value, store}`: the unit's value is `value` over the cells, masked to `width` bits; `store` (reading `value`) writes a value back, run only when the cells do not already give it; each cell state the firings reach gives its own value, and `store` inverts `value` on those states |
| `scope` | `global` for one cell shared by the voices, owned by the voice whose start opened it |
| `fire` | the actions (or proc names) of one firing |
| `pre`, `post` | the functions that bound one firing (`post` defaults to `pre`): `v` is read before `pre` runs, the expression's value is stored after `post` |
| `x` | an expression over the voice's cells giving the expression a start selects; 0 leaves the playroutine's own firing |
| `hooks` | the starts: `{at: function, written: [cells], key: [cells], pointer: [cells]}`; the unit restarts when the function writes a `written` cell, changes the `key` cells, or writes a `pointer` to a program that sets one of `program.fields` |
| `program` | `{pointer, record, fields}`: the program a `pointer` selects and the fields that make it a start |
| `mirror` | a cell that takes the cell's value an expression stores when it held the cell's own |
| `label` | the cells that group a unit's occurrences for the lifter |
| `relative` | cells a start reads as `N` plus their offset from the note |
| `site` | where the lifter writes the expression: `{record, index, field, clear, first, when, row}` (`clear`: fields the expression replaces; `first`: fields set on the program's first row; `when`: over the start's cells, else the event's command; `row`: the site is the program row the start runs, which holds the expression of its one firing) |
| `retire` | `{record, clear}`: native fields the unit's expressions replace and the document does not write; the lifter clears them in every row of `record`, and the replay proves the song unchanged |
| `command` | the command code that names an expression (`!freq`) |
| `commands` | the native command codes the lifter replaces by `command` |

A function is named `procs.<proc>`, `records.<record>.<field>[.<index>]` or
`record_types.<type>.<template>`.

### The cursor

The player keeps one cursor per voice and the playroutine decides when it moves.
`fetch` reads the voice's next step and sets these voice pseudo-cells:

| cell | value |
|---|---|
| `ev_kind` | 0 empty, 1 note, 2 off, 3 on |
| `ev_note` | note + transpose |
| `ev_instr` | instrument index + `base`, or 0 when absent |
| `ev_cmd`, `ev_arg` | first command and argument (an index for a reference), or 0 and 0 |
| `ev_cmd2`, `ev_arg2` | second command and argument, or 0 and 0 |
| `at_phrase_start` | 1 when the next `fetch` reads step 0 of a phrase |
| `ended` | 1 once the voice has stopped |

### Ticks, the ghost and the canonical flush

Each tick, the player runs `init` (first tick) or `tick` (every later tick). Then it
evaluates the ghost, masks each value to 8 bits, and flushes the registers that
changed since the last flush, in GoatTracker 2's buffered order (`BUFFEREDWRITES`:
the filter step first, then `mt_loadregs`). A register with no ghost entry keeps its
value.

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

### Checking against the native player

The player starts from a zeroed SID. `unitracker-play --check` compares a voice's seven
registers with the native player's from the tick the voice's `onset` cell first reads 1,
and the filter and volume registers (`$D415`-`$D418`) from the earliest onset. What a
native player writes before then is its own initialisation, not the song's, and the
format does not carry it: a song's text carries only what the composer's rows and
definitions imply.

## The internal representation

`unitracker.format.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records with a `next` field per program row,
per-voice tracks of plays, phrases of event dicts, the expressions as
`exprs: {index: text}`, and the full playroutine and tables. Lifters produce this
representation and `songio.dumps` writes it.

## Lifting

A lifter (`unitracker.lift.backends.lift`, dispatching to `goattracker.lift`, `defmon.lift` and
`sidwizard.lift`) decodes a native tune and writes each native modulation as an expression;
`expressions=False` returns the internal representation with the native modulations. The
conversion (`unitracker.modfit.convert`, see [modfit.md](modfit.md)) must leave the song playing
the native register grid exactly, each voice from its onset (see Checking against the native
player); a start whose expression is not proved, and a native row with no written form, fail the
lift, naming them. Each playroutine's file lists what its lifter does not cover.

| playroutine | tune | step |
|---|---|---|
| [`goattracker2`](playroutines/goattracker2.md) | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row |
| [`sidwizard`](playroutines/sidwizard.md) | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row |
| [`defmon`](playroutines/defmon.md) | defMON `$1000` replay `.sid` (pydefmon) | one tick |
