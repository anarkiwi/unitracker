# unitracker song format (v1)

A unitracker song is one YAML document. A backend lifts a native tune into it
(`unitracker-lift` reads GoatTracker 2 and defMON), and the generic player
(`unitracker-play`) plays it on an emulated SID. The document is laid out so that any
prefix of it is a playable partial song: a short header comes first, then one
timeline in which every instrument, program and phrase is defined in the row where it
is first used. Everything a player does is in its **playroutine**, which the song
references from the library in `src/unitracker/playroutines/`.

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1
playroutine:
  ref: goattracker2
  constants: {deftempo: 11}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
hints: {ticks_per_step: 16, phrase_steps: 48}
song:
  - def:
      ins:
        i1: {ad: 1, sr: 136, wave: [[233], [233], [129, 196], [65], end], pulse: [[132], [32, 16], [64,
              240], [32, 16], {loop: 1}], filter: [[144, 193, 128], end], gt: 4, fw: 9}
        ...
      wavetab:
        w1: [[233], [233], [81], [65], [1, 5], [1, 8], [1], {loop: 4}]
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!8:@w1*3, off*3, G4!8:@w1*3, ...]
        p3: [off!15:16*48]
    play: [p1, p2, p3]
  - def:
      ...
    play: [p4, p5, p6]
loop: 0
```

## Document

Top-level keys, in this order:

| key | content |
|---|---|
| `unitracker` | `1` |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (see Embedding) |
| `meta` | `title`, `author`, `released` |
| `hints` | derived facts, such as `ticks_per_step` and `phrase_steps`; the player ignores them |
| `clock`, `voices`, `registers` | only where they differ from the playroutine's `defaults`; `registers` is `{index: value}` |
| `song` | the timeline: a list of rows |
| `loop` | the row the song loops to, or `~` to end |

`playroutine.constants` lists only the constants that differ from the library's.
`playroutine.tables` patches the library's tables: `{index: value}` for the entries that
differ, or a full list when the length differs.

The patch holds only entries the song can read. The writer plays the song, recording
every table index read, until its complete state (all cells, cursors and registers)
recurs. Playback is deterministic, so no later tick reads anything new, and an entry
never read cannot change the output; the library default stands in for it. If the
state does not recur within a bound, the patch is written whole.

### Embedding

`unitracker-lift --embed` writes the playroutine in full instead of `ref`, with its
`tables` complete, so the file plays without the library.

## Timeline

Each row is `{def: {...}, play: [entry per voice]}`; `def` is present only when
something is first used in that row. An entry is:

| entry | meaning |
|---|---|
| `p3` | the voice starts phrase `p3` |
| `p3+5`, `p3-2` | the voice starts phrase `p3` transposed by that many semitones |
| `end` | the voice stops |
| `~` | the voice is still playing its phrase |

A row starts at the earliest end of the voices' current phrases. Every voice whose
phrase ends there must have an entry, and every other voice must be `~`; readers
reject anything else. Repeats are written out as rows. When the voices' loops have
different lengths, the rows run until all of them are back in the same state together,
and `loop` is the row where that state first occurred.

## Definitions

`def` maps a record type, or `phrase`, to `{name: value}`. Names are the record
type's prefix and a number, given in first-use order (`i1`, `i2`, ..., `w1`, ...;
phrases `p1`, ...). The reader allocates each type's indices in definition order from
the type's `first` index, so a name stands for whatever index the playroutine needs.

- **Rows** are written in the type's `form`. A `list` row holds the fields in
  declaration order, with trailing defaults left out: GoatTracker wave rows are `[w, n]`
  in its own column bytes. A `map` row holds the fields that differ from their
  defaults: `{ad: 1, sr: 136, gt: 4}`.
- **Programs** are the rows of a chained type, in the order they run, followed by
  `end` or `{loop: k}` (k is a row of the same program). A program is cut from the
  player's chained tables at every entry point. A tail two programs share is written
  in both, which is exact because rows are deterministic.
- **References.** A field that references another record holds a name. A program that
  only one instrument uses is written inline in that instrument instead
  (`wave: [[233], [65], end]`).
- **Fixed** record types, such as defMON's initial voice state, keep their indices and
  are defined in the first row: `vinit: {0: {...}, 1: {...}}`.

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command:arg][*steps]
```

- `note` is a note name such as `C2`, `A#3` or `C10` (semitone index 24 is `C2`), `-`
  for no note, `off` (gate off) or `on` (gate on).
- `.i3` names the instrument. For defMON it names a sidTAB program.
- `!8:@w1` is command 8 with an argument. The argument is a number, or `@name` when
  the playroutine declares that the command references a record type.
- `*3` makes the event last 3 steps; the steps after the first are empty.

## Canonical form

The writer produces one text per song:

- names are given in first-use order;
- records and rows the song never reaches are dropped;
- identical phrases, and phrases that are equal up to a transpose, are merged, and
  the transpose moves into the play entry;
- runs of empty steps become `*steps`;
- no-op commands are dropped.

Writing what the reader returns gives the same bytes.

## The playroutine

The library holds one definition per player build:

```yaml
name: str
constants: {name: int}
defaults: {clock: {cycles_per_tick: 19656, clock_hz: 985248}, voices: 3, registers: [25 ints]}
tables: {name: [ints]}                    # player-build data; reads out of range give 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
commands: {8: wavetab, ...}               # commands whose argument references a record type
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
```

### Record types

| key | meaning |
|---|---|
| `fields` | a list of names, or `{name: default}`; a missing field reads as its default |
| `form` | `list` (default) or `map` |
| `prefix` | the name prefix |
| `chain` | the field holding the next row: the type is a table of programs |
| `none` | the index that means "no row"; references equal to it are plain numbers |
| `stop` | an expression over a row's fields after which a program ends |
| `first`, `max` | the indices the reader allocates (default `none + 1`, or 0; up to 255) |
| `pin` | indices the playroutine reads directly; they are defined first |
| `fixed` | the rows keep their indices |
| `refs` | `{field: type}`, or `{field: [{type: t, when: expr}, ...]}` for a reference that depends on the row |
| any other key | a template: an action list run for a row |

A template reads the row it runs for as `row.<field>`. `run: {record: r, index: i,
field: f}` runs record type `r`'s template `f` for row `i` when that row exists.
GoatTracker's wave rows, for example, decode their bytes in the template:

```yaml
wavetab:
  prefix: w
  none: 0
  chain: next
  fields: [w, n, next]
  do:
    - if: "row.w >= 0xF0"
      then: [{set: {arg: row.n}}, {run: {record: wavecmd, index: "row.w & 0x0F", field: do}}, ...]
    ...
```

### Cells and expressions

Every assignment wraps to the target cell's `width`, using two's complement for
negative values. Expressions use Python syntax, restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`, `record[i].field` and, in a template, `row.field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`.

Arithmetic is unbounded; only assignment wraps.

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
clamp: true
on_bound: [actions]
```

```
v0 = cell
if toward given:
    if v0 == T: done
    if v0 < T: s = step, max = T  else: s = -step, min = T
v  = wrap(v0 + s, width(cell))           # bounds are tested after wrapping, as players do
hit = v > max (if max given) or v < min (if min given)
if hit and clamp: v = the bound crossed
cell = v
if hit: run on_bound
```

### The cursor

The player keeps one cursor per voice and the playroutine decides when it moves.
`fetch` reads the voice's next step and sets these voice pseudo-cells:

| cell | value |
|---|---|
| `ev_kind` | 0 empty, 1 note, 2 off, 3 on |
| `ev_note` | note + transpose |
| `ev_instr` | instrument index + `base`, or 0 when absent |
| `ev_cmd`, `ev_arg` | command and argument (an index for a reference), or 0 and 0 |
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

## The internal representation

`unitracker.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records with a `next` field per program row,
per-voice tracks of plays, phrases of event dicts, and the full playroutine and
tables. Lifters produce this representation and `songio.dumps` writes it.

## Backends

| backend | tune | step | lifted from |
|---|---|---|---|
| `goattracker2` | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row | the decompiled song and the build's constants |
| `defmon` | defMON `$1000` replay `.sid` (pydefmon) | one tick | the replay's memory after init |

**GoatTracker 2.** The playroutine counts ticks per row itself, and `fetch` runs on its
gate-timer lookahead. Instrument 1 is pinned because every voice starts on it. A
filter parameter row carries the following cutoff row's value as `cut`, because the
player reads both in one tick.

**defMON.** The arranger and pattern counters are lifted as timing. Each song step
becomes one phrase per voice, all of the same length in ticks, with each event on the
tick the replay applies it. The playroutine fetches every tick and maps the event
fields as follows:

- `.t3` is the sidTAB program sidcall layer A starts;
- `!1:@t5` arms layer B;
- the note is a GATE_N note.

sidTAB rows are maps of the columns present (`dl`, `wgh`, `wgl`, `ad`, `sr`, `tr`,
`af`, `pw`, `ps`, `re`, `fv`, `cp`, `acid`), applied in the replay's bitmap-walk
order. The replay probes `$D41B` at init to tell the SID models apart; the result
becomes the playroutine constants `floor` and `dbl`. The ghost masks pulse-width high
to a nibble, as the reference grid does.
