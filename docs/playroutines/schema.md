# The playroutine

The library holds one decoding declaration per player build, `playroutines/<name>.yaml`: the
native player as the lifter's VM runs it, and the lifter's mapping of its rows and commands to
the shared row keys and commands.

```yaml
name: str                                 # the file's name, which the loader sets
constants: {name: int}
defaults: {clock: {cycles_per_tick: 19656, clock_hz: 985248}, voices: 3, tempo: 6}   # optional; the lifted song's unless the tune gives its own (no tempo by default)
tables: {name: [ints]}                    # player-build data; reads out of range give 0
note_offset: 0                            # frequency-table index of format note 0; default 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
                                          # default: the type decoded as ins, base 0
                                          # event: {type, column, commands, restart}: see Instrument columns
tempo: {cells: {tl: a, tr: b}}           # the initial tempo (see Tempo)
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
| `optional` | `[field, ...]`: more fields, which read as -1, "absent", when missing; one that is a shared key gives each codec shape that key, left out when absent (see [Codecs](#codecs)) |
| `form` | `list` (default) or `map` |
| `writes_as` | the shared record type the lifter decodes it as (default: its own name) |
| `prefix` | the prefix of its records' names (default: the first letter of the shared record type); distinct from the other types' and from the phrases' `p` |
| `chain` | the field holding the next row: the type is a table of programs (default: `next` when the type has a `next` field; `null` for none) |
| `none` | the index that means "no row"; references equal to it are plain numbers (default 0, none for a `fixed` type; `null`: every index is a row) |
| `stop` | an expression over a row's fields after which a program ends |
| `max` | the last row index (rows from `none + 1`, or 0; default 255) |
| `pin` | indices the playroutine reads directly; they are defined first |
| `fixed` | the rows keep their indices |
| `refs` | `{field: type}`, or `{field: [{type: t, when: expr}, ...]}` for a reference that depends on the row |
| `codec` | shapes mapping rows to shared row keys (see [Codecs](#codecs)) |
| `end` | raw fields set on a program's last row when it is followed by `end` |
| `expr` | the field holding a row's modulator expression, a reference to `x` with no `refs` entry; a program of one row that sets only it decodes as the expression |
| `adds` | `{key: k}`: a row whose only key is `key`, or none, sets nothing and lasts `key + k` ticks: a run of them decodes as one row as long as one row lasts (then the rest), and an expression `v` on a first row of only `key` and the expression's key, in a program whose other rows set none of that key, is left out. Default: none |
| `uniform` | `[field, ...]`: fields every row of the song that sets them sets to one value, as a player that keeps one copy plays them; the lifter refuses a song that sets one to two |
| `unwritten` | `[field, ...]`: raw fields with no shared key; the lifter refuses a row in which one differs from its default |
| any other key | a template: an action list run for a row |

The record type `x` holds the lifter's expressions, and a playroutine cannot declare it.

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

A codec shape is the lifter's mapping of one native row to shared row keys. It has a `when` over
the raw fields, the playroutine's constants, and for a program row its place (`first` and `last`
are 1 for the program's first row and for the row before its `end`); `keys`
(`{key: {value, byte, omit, abs, rel, expr, eor}}`: the shared key's value from the raw fields,
when to leave it out, for `note` whether it is absolute, for `route` its relation to the
program's voice (1 adds it, -1 removes it, default 0), the raw field holding the key's
expression, and for `ctrl` its mask as `{value, omit}`); and `raw`, the inverse (each raw field
from the keys, with `has_<key>`, `note_abs`, `route_rel`, for a mask `has_eor` and `eor`, and
each expression key's raw field, the expression's reference or 0). A shape has no other key.
The lifter maps a row by the first shape whose `when` holds, and accepts the mapping only when
`raw` gives the row back.

An `optional` field `f` that is a shared key gives each shape the key
`f: {value: f, omit: "f < 0"}` and the raw field `f: "f if has_f else -1"`; a shape that gives
key `f` itself gives its raw field too, and a raw field `f` the shape gives replaces the
generated one. A key with a shared default (`hold`: 1) is left out at that value unless it
gives its own `omit`, and `raw` reads it as the default when it is left out (`hold: "w + 1"`
with `w: "hold - 1"`). A key with `expr` maps to the expression when that field is set; a key
with both `value` and `expr` maps an integer to a value and a string to an expression.

| key form | native field |
|---|---|
| `cut: {byte: b}` | a cutoff the player stores as its top byte: the value is `b << 3`, and `raw` reads `cut >> 3` |
| `note: {byte: f, none: n, abs: [lo, hi]}` | the note byte `f`: `n` is no note, a byte from `lo` to `hi` (default `[0x80, 0xFF]`) an absolute note in its low 7 bits, any other byte a relative note, two's complement in 7 bits when the absolute range reaches $FF, else in 8 (SID-Wizard's `abs: [0x81, 0xDF]` makes $E0-$FF relative down); it gives `f`'s `raw` too |
| `{ref: type, field: f}` | the reference `f` holds, as the record's name (`run: pw1`) |
| `{none: true}` | `none` (`pw: none`) |

A shape `{command: c, arg: a, skip: [codes]}` maps a row that runs a command to that command's
key: it stands for one shape per `commands` entry named a shared row key (not one with `ref: x`,
`nibble`, `args` or `inline`) whose code is not in `skip`, fitting the rows whose `c` is the code
and `a` an argument the entry takes (for a reference, not its type's `none`), the key taking the
entry's argument; its `raw` reads `command` as the code and `arg` as the raw argument
(`{command: "w & 0x0F", arg: n, raw: {w: "0xF0 | command", n: arg}}`).

A `commands` entry maps a native command code to a shared command. It has a `name`, an optional
`ref` (a record type, or `x` for an expression), and for a plain argument optional `when`, `arg`
(raw to shared) and `raw` (shared to raw), checked the same way, or for `cut` `byte` in place of
`arg` (`{name: cut, byte: arg}`: the value is `arg << 3`, and `raw` reads `arg` as `cut >> 3`);
`args` (`{a: expr, b: expr}`) maps the argument to several values joined by `/`, which `raw`
reads by those names (`!tempo:5/7`).

| entry key | meaning |
|---|---|
| `lasts` | for `freq`, `step`: the native `!freq` ends on a step without a command |
| `also` | `name:arg`, another command whose native effect the code has too: the code decodes as both (`!filter:none!resfilt:0/0`) |
| `inline` | `true` with a `ref`: the referenced row's fields, each a byte, joined by `/` (`!tempo:6/7`), `when` over the row's fields; the type has no records of its own in the lifter's song |
| `column` | the event column the command decodes into, `instrument` (event slot 0) or `fx` (slot 1), or a list of them of which it takes the first the event leaves free (an event that names an instrument fills `instrument`); a named entry without one decodes into `fx`, and a column holds one command |
| `nibble` | `{name: ad, nibble: s, column: c}`, an envelope command: the code sets the nibble at shift `s` of the register `name` over the instrument's field `name`, played from the slot of column `c`, and decodes as the byte it writes (see [Envelope commands](#envelope-commands)) |

A program `ref` entry's argument `none` is the type's `none`, the stop command of its slot
(`!run:none`, `!pw:none`, `!filter:none`). Codec expressions may read the song's tables and the
playroutine's functions.

## Tempo

A song's tempo is `n` ticks per step or a swing pair `[a, b]`. The playroutine's `tempo` gives
the cells that hold it, `cells` (`{cell: expr}`), each expression over `a` and `b` (`b` is `a` for
one tempo): the lifter reads the tune's tempo from them after its init, and the VM assigns them
over the cells' `init` before the first tick. A playroutine without `tempo` has none.

## Lifter declarations

The lifter decodes these native mechanisms into the shared row keys and commands, whose meaning
is unitracker-player's [semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md).

### Lifetime

`lifetime: {pw: {flag: {flags: 0x40}}, filter: {...}}` declares a player that keeps a note's
pulse or filter program by an instrument bit: `flag` is the instrument field and bit mask, and
`field` the instrument key of the program (default: the slot's name). A note whose instrument
sets the bit carries `!pw` / `!filter`; a note that names no instrument plays the voice's last
one, and a legato note restarts nothing.

A `commands` entry `{name: freq, lasts: step}` declares a native `!freq` that ends on a step
carrying no command: each such step after a `!freq` decodes as `!freq:none`.

### Envelope commands

A `nibble` entry (see [Codecs](#codecs)) sets one nibble over the instrument's byte; the lifter
decodes it as the byte it writes, from the voice's instrument. A phrase whose bytes differ
between the places it plays becomes one phrase per place.

### Instrument columns

`instrument: {type: t, base: b, event: {type: ins, column: k, commands: {c: f}}}` declares a
player whose events arm programs directly, with no native instrument: an event's instrument
column (record `t` at index value - `b`) is field `k` of an `ins` record and the argument of its
command `c` field `f`. The lifter names one instrument per combination of programs the events
arm, and the event names that instrument.

`event: {..., restart: c}` declares a player whose notes arm the hard restart on the steps before
them: the restart of a note's instrument is native command `c` on the step its lookahead rows'
holds put before the note, running those rows as a program of the command's record type. A
`restart` program's rows use `hold`, `ctrl`, `gate`, `ad` and `sr`; a row's place in it (`first`,
`last`) is part of its codec shape.

## Cells and expressions

Every assignment wraps to the target cell's `width`, using two's complement for
negative values. The voice cell `onset` is 1 once the voice's registers hold its first note
(see [Checking against the native player](#checking-against-the-native-player)); every
library playroutine declares it, and a voice of a playroutine without it is compared from its
first tick. Expressions use Python syntax, restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`, `record[i].field` and, in a template, `row.field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`, and to the builtins `s8(x)` and `s16(x)`, byte and word `x` read as two's
  complement.

Arithmetic is unbounded; only assignment wraps. These are the playroutine's expressions; the lifter's
modulator expressions are the grammar's `expr`
([`canonical.lark`](https://github.com/anarkiwi/unitracker-grammar/blob/main/src/unitracker_grammar/canonical.lark)),
evaluated as unitracker-player's
[Modulators](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#modulators)
say.

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
| `site` | where the lifter writes the expression: `{record, index, field, clear, first, when, row}` (`clear`: `{field: value}`, each an expression over the row's raw fields, the fields the expression replaces in every row of the program it writes; a field it does not name keeps its value, as a row's length; `first`: fields set on the program's first row; `when`: over the start's cells, else the event's command; `row`: the site is the program row the start runs, which holds the expression of its one firing). A start reads a chained type's site on the program's first row, so a codec shape holding the site field's expression fits a later row only without one, unless the site is a `row` site |
| `init` | `{record, index, field, clear, x}`: the playroutine's init starts the unit, its first firing the one the init runs (from the firing's last function when the init enters it there); the lifter writes that start's expression in `record[index].field` (a fixed record), clearing `clear`, and the start selects `x` (default `record[index].field`) |
| `retire` | `{record, clear}`: native fields the unit's expressions replace, which have no shared key; the lifter clears them in every row of `record`, and the replay proves the song unchanged |
| `commands` | the native command codes the lifter replaces by the command named `key` that references `x` (`!freq`) |

A function is `{proc: p}`, `{record: r, field: f}` (every row's action list in field `f`, with
`index: i` row `i`'s, or with `index: [i, ...]` those rows') or `{type: t, template: f}`. A `fire`
entry names the function it runs: a proc name `{proc}`, a `run` with `field` `{record, field}`, a
`run` with `template` `{type, template}`, or the function itself, which the lifter runs at the index
of the one `run` that calls it. A firing whose every call is a start fires and starts at the same
function (GoatTracker's wavetable steps: `fire: [{record: wavecmd, field: do, index: [1, 2, 3, 4]}]`
with a `row` site, whose hooks default to `[{at: {record: wavecmd, field: do, index: [1, 2, 3, 4]}}]`).

## The cursor

The VM keeps one cursor per voice and the playroutine decides when it moves.
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

Each tick, the VM runs `init` (first tick) or `tick` (every later tick). Then it
evaluates the ghost, masks each value to 8 bits, and flushes the registers that
changed since the last flush, in the canonical order below: the filter and volume registers,
then each voice's pulse width, envelope, frequency and, last, its control register. A register
with no ghost entry keeps its value. An entry `r` naming a cell wider than 8 bits (`0: freq`) or a pair (`0: "fhi:flo"`) gives
register `r` the low byte and `r + 1` the high byte, unless `r + 1` has an entry of its own.

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

## Checking against the native player

The lifter's VM starts from a zeroed SID and replays the lifter's song against the native
player's register grid: each voice's seven registers from the tick its `onset` cell first reads
1, and the filter and volume registers (`$D415`-`$D418`) from the earliest onset. What a native
player writes before then is its own initialisation, which the lifted song does not carry. This
replay is the criterion by which the normalisations and the conversion are proved; the lift's
acceptance is its `verify` stage (see [Lifting](lifting.md)): the generic document played on
unitracker-player against the native grid.
