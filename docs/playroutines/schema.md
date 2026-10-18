# The playroutine

The library holds one definition per player build, named by its file
(`playroutines/<name>.yaml`); an embedded definition carries its `name`:

```yaml
name: str                                 # embedded definitions only
constants: {name: int}
defaults: {clock: {cycles_per_tick: 19656, clock_hz: 985248}, voices: 3, tempo: 6}   # optional; a song's own unless it says otherwise (no tempo by default)
tables: {name: [ints]}                    # player-build data; reads out of range give 0
note_offset: 0                            # frequency-table index of format note 0; default 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
                                          # default: the type written as ins, base 0
                                          # event: {type, column, commands, restart}: see Instrument columns
tempo: {cells: {tl: a, tr: b}, when: "3 <= a <= 127"}   # the initial tempo (see Tempo)
lifetime: {pw: {flag: {flags: 0x40}}, filter: {flag: {flags: 0x80}}}   # !pw and !filter (see Lifetime)
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
  voice: {0: expr, 1: expr, ..., 6: expr} # offsets within a voice; a word cell or "hi:lo" fills two
modulators: {freq: ..., notes: [...], units: [...]}   # see Modulator declarations
```

## Record types

| key | meaning |
|---|---|
| `fields` | a list of names, or `{name: default}`; a missing field reads as its default |
| `optional` | `[field, ...]`: more fields, which read as -1, "absent", when missing; one that is a shared key gives each codec shape that key, left out when absent, and reads back as it (see [Codecs](#codecs)) |
| `form` | `list` (default) or `map` |
| `writes_as` | the record type a document writes it as (default: its own name; see [Definitions](../format/document.md#definitions)) |
| `prefix` | the name prefix (default: the first letter of the written name); distinct from the other types' and from the phrases' `p` |
| `chain` | the field holding the next row: the type is a table of programs (default: `next` when the type has a `next` field; `null` for none) |
| `none` | the index that means "no row"; references equal to it are plain numbers (default 0, none for a `fixed` type; `null`: every index is a row) |
| `stop` | an expression over a row's fields after which a program ends |
| `max` | the last index the reader allocates (from `none + 1`, or 0; default 255) |
| `pin` | indices the playroutine reads directly; they are defined first |
| `fixed` | the rows keep their indices |
| `refs` | `{field: type}`, or `{field: [{type: t, when: expr}, ...]}` for a reference that depends on the row |
| `codec` | shapes writing rows as shared keys (see [Codecs](#codecs)) |
| `end` | raw fields set on a program's last row when it is followed by `end` |
| `expr` | the field holding a row's modulator expression, a reference to `x` with no `refs` entry; a program of one row that sets only it is written as the expression |
| `uniform` | `[field, ...]`: fields every row of the song that sets them sets to one value, as a player that keeps one copy plays them; the reader and the writer refuse rows that set one to two |
| `unwritten` | `[field, ...]`: raw fields with no written form; the reader refuses them as unknown keys (`unknown key speed in ins`) and the writer refuses a row in which one differs from its default |
| any other key | a template: an action list run for a row |

The record type `x` is the format's own: it holds the expressions, and a playroutine
cannot declare it.

A template reads the row it runs for as `row.<field>`. `run: {record: r, index: i,
template: t}` runs record type `r`'s template `t` for row `i` when that row exists.
`field` always names a raw field: `run: {record: r, index: i, field: f}` runs the action list
row `i` stores in field `f`.
A control-program table, for example, decodes its bytes in a template:

```yaml
ctrltab:
  writes_as: ctrl
  fields: [w, n, next]             # chained by next; none 0
  do:
    - if: "row.w >= 0xF0"
      then: [{set: {cmdarg: row.n}}, {run: {record: cmd, index: "row.w & 0x0F", field: do}}, ...]
    ...
```

## Codecs

A codec shape has a `when` over the raw fields, the playroutine's constants, and for a program row
its place: `first` and `last` are 1 for the program's first row and for the row before its `end`;
`keys` (`{key: {value, byte, omit, abs, rel, expr, eor}}`: the shared key's value from the raw fields, when
to leave it out, for `note` whether it is absolute, for `route` its relation to the program's voice
(1 adds it, -1 removes it, default 0), the raw field holding the key's expression, and for `ctrl`
its mask as `{value, omit}`), `raw` (each raw field from the written keys, with `has_<key>`,
`note_abs`, `route_rel`, for a mask `has_eor` and `eor`, and each expression key's raw field, the
expression's reference or 0, defined) and `keep` (raw fields written under their own names; a kept
reference is written as a name). An `optional` field `f` that is a shared key gives each shape the
key `f: {value: f, omit: "f < 0"}` and the raw field `f: "f if has_f else -1"`; a shape that gives
key `f` itself gives its raw field too, and a raw field `f` the shape gives replaces the generated one.
A key with a vocabulary default (`hold`: 1) is left out at that value unless it gives its own
`omit`, and `raw` reads it as the default when it is left out (`hold: "w + 1"` with `w: "hold - 1"`). A key with `expr` is written as the expression
when that field is set; a key with both `value` and `expr` reads an integer as a value and a string
as an expression. A `cut` key given as `{byte: b}` is a cutoff the player stores as its top
byte: its value is `b << 3`, and `raw` reads `cut` as that byte, the written value `>> 3`; a
written `cut` with any of its low three bits set does not read back. A `note` key given as `{byte:
f, none: n, abs: [lo, hi]}` is the note byte raw field `f` holds: `n` is no note, a byte from `lo`
to `hi` (default `[0x80, 0xFF]`) an absolute note in its low 7 bits, and any other byte a relative
note, two's complement in 7 bits when the absolute range reaches $FF, else in 8 (SID-Wizard's
`abs: [0x81, 0xDF]` makes $E0-$FF relative down); it gives `f`'s `raw` too. A key given as `{ref: type, field: f}` is the reference raw field `f` holds,
written as the record's name (`run: pw1`); the reader takes the shape whose `ref` is the type the
name has. A key given as `{none: true}` is written `none` (`pw: none`) and reads only from `none`.
A shape `{command: c, arg: a, skip: [codes]}` writes a row that runs a command as that command:
it stands for one shape per `commands` entry named a shared row key (not one with `ref: x`,
`nibble`, `args` or `inline`) whose code is not in `skip`, fitting the rows whose `c` is the code
and `a` an argument the entry takes (for a reference, not its type's `none`), and writing the key
as the entry writes its argument; its `raw` reads `command` as the code and `arg` as the raw
argument (`{command: "w & 0x0F", arg: n, raw: {w: "0xF0 | command", n: arg}}`).
The writer uses the first shape whose `when` holds and refuses a row that would not read back; a
`map` row no shape fits is an error. The reader accepts a written row only if encoding its inverse
gives the row again. A command entry has a `name`, an optional `ref` (a record type, or `x` for an
expression), and for a plain argument optional `when`, `arg` (raw to written) and `raw` (written to
raw), checked the same way, or for `cut` `byte` in place of `arg` (`{name: cut, byte: arg}`: the
written value is `arg << 3`, and `raw` reads `arg` as the written value `>> 3`); `args` (`{a: expr, b: expr}`) writes the argument as several values
joined by `/`, which `raw` reads by those names (`!tempo:5/7`). A `freq` entry's `lasts: step` says the
native command ends on a step that carries no command (see [Lifetime of
`!freq`](../format/vocabulary.md#lifetime-of-freq)). An entry's `also` (`name:arg`) is
another command whose native effect the code has too: the event writes it beside the command,
and the reader reads the two as the one code and refuses the command without it
(`!filter:none!resfilt:0/0`). A `ref` entry with `inline: true`
writes the referenced row's fields, each a byte, joined by `/` (`!tempo:6/7`), its `when` over
the row's fields; the reader allocates one row per distinct argument, and the type has no
definitions of its own (`unknown record type 'speedtab'`). A program `ref` entry's argument `none`
is the type's `none`, written as the stop command of its slot (`!run:none`, `!pw:none`,
`!filter:none`). An entry's `column`
is the event column its command is read into, `instrument` (event slot 0) or `fx` (slot 1), or a
list of them of which it takes the first the event leaves free (an event that names an instrument
fills `instrument`); a named entry without one is read into `fx`. A column holds one command, and
an event with two in one is refused. An entry
`{name: ad, nibble: s, column: c}` is an envelope command: the code sets the nibble at shift `s` of
the register `name` over the instrument's field `name`, played from the slot of column `c`, and is
written as the byte it writes (see [Envelope commands](../format/vocabulary.md#envelope-commands)).

Codec expressions may read the song's tables and the
playroutine's functions. A list row a shape covers is rejected: it has a shared-key form.

## Tempo

A song's `tempo` (see [Document](../format/document.md)) is `n` ticks per step or a swing pair
`[a, b]`. The playroutine's `tempo` gives the cells it starts, `cells` (`{cell: expr}`), each
expression over `a` and `b` (`b` is `a` for one tempo), assigned over the cells' `init` before the
first tick; `when` is the tempos the player plays. A
playroutine without `tempo` has none, and a song that names one is refused.

## Cells and expressions

Every assignment wraps to the target cell's `width`, using two's complement for
negative values. The voice cell `onset` is 1 once the voice's registers hold its first note
(see [Checking against the native player](#checking-against-the-native-player)); every
library playroutine declares it, and a voice of a playroutine without it is checked from its
first tick. Expressions use Python syntax, restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`, `record[i].field` and, in a template, `row.field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`, and to the builtins `s8(x)` and `s16(x)`, byte and word `x` read as two's
  complement.

Arithmetic is unbounded; only assignment wraps. These are the playroutine's expressions; a song's
modulator expressions use the grammar of [Modulators](../modulation/expressions.md#grammar).

## Actions

| action | meaning |
|---|---|
| `set: {cell: expr, ...}` | Assign in order, each wrapped to its cell's width. |
| `if: expr`, `then: [...]`, `else: [...]` | Conditional. |
| `call: proc`, `with: {cell: expr}` | Assign the `with` cells, then run the proc. |
| `run: {record: r, index: expr, template: t}` | Run record type `r`'s template `t` for row `index`. `run: {record: r, index: expr, field: f}` runs the action list row `index` stores in field `f`. |
| `for_voices: [...]` | Run the list once per voice, in voice order, with that voice's cells bound. |
| `fetch: true` | Read this voice's next step (see [The cursor](#the-cursor)). |
| `return: true` | Leave the innermost proc, template, record action list, `for_voices` body, or `init`/`tick`. |
| `accumulate: cell` | The bounded accumulator. |

## The bounded accumulator

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

## Modulator declarations

`modulators` declares the player's units and what their expressions read.

| key | meaning |
|---|---|
| `freq` | the word `F(k)` reads at index `k + note_offset`: `{word: table}`, or `{hi: table, lo: table}` joined as `hi << 8 \| lo`; or the name of a one-argument function that reads one of these at its argument (`freq: nfreq`) |
| `notes` | the voice cells that hold the note; `N` is the first, less `note_offset` |
| `transpose` | a voice cell added to the note for `N` |
| `legato` | `{cell}`: the cell set while a note does not retrigger, which the `legato` command (`!legato`) sets |
| `tables` | `{name: {entry: expr, size: n}}`: the tables `name[k]` expressions read; `entry` reads `i` and the playroutine's tables. `{name: {function: f, size: n}}` takes `entry` from the one-argument function `f` and adds `f`'s rewrite over the table to `functions` |
| `functions` | `{name: expr}`: playroutine functions rewritten over the named tables, for analysis |
| `units` | the units, below |

| unit key | meaning |
|---|---|
| `name` | the unit's name |
| `key` | the shared key it drives: `freq`, `pw` or `cut` |
| `cell` | the driven cell; a pair `hi:lo[:k]` is the value `hi << k \| lo & (2**k - 1)` (k 8 when omitted) |
| `chip` | `{width, offset, value, store}`: the unit's value is `value` over the cells, masked to `width` bits; `store` (reading `value`) writes a value back, run only when the cells do not already give it; each cell state the firings reach gives its own value, and `store` inverts `value` on those states. `value` defaults to the cells less `offset` (an expression, default 0), a cell narrower than `width` holding the value's top bits, and `store` to its inverse |
| `scope` | `global` for one cell shared by the voices, owned by the voice whose start opened it (default: `global` for a global cell) |
| `fire` | one firing: proc names, `run` actions and functions; `v` is read before the first entry's function runs and the expression's value is stored after the last's |
| `x` | an expression over the voice's cells giving the expression a start selects; 0 leaves the playroutine's own firing (default: the site's field, `record[index].field`) |
| `hooks` | the starts: `{at: function, written: [cells], changed: [cells], pointer: [cells]}`; the unit restarts when the function writes a `written` cell, changes the `changed` cells, or writes a `pointer` to a program that sets one of `program.fields`; a hook with none of the three restarts it on every call, before the call runs. Default: each function outside the firing (its functions and what they call) that writes the unit's pointer, `program.pointer` or else the site's `index`, as a `pointer` hook with a `program` and a `written` one without; with a `row` site, each function of the firing |
| `program` | `{pointer, record, fields}`: the program a `pointer` selects and the fields that make it a start (`pointer` and `record` default to the site's `index` and `record`) |
| `mirror` | a cell that takes the cell's value an expression stores when it held the cell's own |
| `label` | the cells that group a unit's occurrences for the lifter (default: the site's `index`) |
| `relative` | cells a start reads as `N` plus their offset from the note |
| `site` | where the lifter writes the expression: `{record, index, field, clear, first, when, row}` (`clear`: `{field: value}`, each an expression over the row's raw fields, the fields the expression replaces in every row of the program it writes; a field it does not name keeps its value, as a row's length; `first`: fields set on the program's first row; `when`: over the start's cells, else the event's command; `row`: the site is the program row the start runs, which holds the expression of its one firing) |
| `retire` | `{record, clear}`: native fields the unit's expressions replace and the document does not write; the lifter clears them in every row of `record`, and the replay proves the song unchanged |
| `commands` | the native command codes the lifter replaces by the command named `key` that references `x` (`!freq`) |

A function is `{proc: p}`, `{record: r, field: f}` (every row's action list in field `f`, with
`index: i` row `i`'s, or with `index: [i, ...]` those rows') or `{type: t, template: f}`. A `fire`
entry names the function it runs: a proc name `{proc}`, a `run` with `field` `{record, field}`, a
`run` with `template` `{type, template}`, or the function itself, which the lifter runs at the index
of the one `run` that calls it. A firing whose every call is a start fires and starts at the same
function (GoatTracker's wavetable steps: `fire: [{record: wavecmd, field: do, index: [1, 2, 3, 4]}]`
with a `row` site, whose hooks default to `[{at: {record: wavecmd, field: do, index: [1, 2, 3, 4]}}]`).

## The cursor

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

## Ticks, the ghost and the canonical flush

Each tick, the player runs `init` (first tick) or `tick` (every later tick). Then it
evaluates the ghost, masks each value to 8 bits, and flushes the registers that
changed since the last flush, in the canonical order below: the filter and volume registers,
then each voice's pulse width, envelope, frequency and, last, its control register. A register
with no ghost entry keeps its value. An entry `r` naming a cell wider than 8 bits (`0: freq`) or a pair (`0: "fhi:flo"`) gives
register `r` the low byte and `r + 1` the high byte, unless `r + 1` has an entry of its own.

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

## Checking against the native player

The player starts from a zeroed SID. `unitracker-play --check` compares a voice's seven
registers with the native player's from the tick the voice's `onset` cell first reads 1,
and the filter and volume registers (`$D415`-`$D418`) from the earliest onset. What a
native player writes before then is its own initialisation, not the song's, and the
format does not carry it: a song's text carries only what the composer's rows and
definitions imply.
