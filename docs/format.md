# unitracker song format (v1.3)

A unitracker song is one YAML document. A backend lifts a native tune into it
(`unitracker-lift` reads GoatTracker 2, defMON and SID-Wizard), and the generic player
(`unitracker-play`) plays it on an emulated SID. The document is laid out so that any
prefix of it is a playable partial song: a short header comes first, then one
timeline in which every instrument, program and phrase is defined in the row where it
is first used. Everything a player does is in its **playroutine**, which the song
references from the library in `src/unitracker/playroutines/`.

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.3
playroutine: {ref: goattracker2, constants: {deftempo: 11}}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
hints: {ticks_per_step: 16, phrase_steps: 48}
song:
  - def:
      ins:
        i1: {ad: 1, sr: 136, ctrl: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: =68},
            {ctrl: 65, note: +0}, end], pulse: [{pw: 1024}, [32, 16], [64, 240], [32, 16], {loop: 1}],
          filter: [{cut: 1024, resfilt: 193, mode: 16}, end], gt: 4, fw: 9}
        ...
      ctrltab:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0},
          {wait: 1, note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!run:@c1*3, off*3, G4!run:@c1*3, ...]
        p3: [off!tempo:16*48]
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
| `unitracker` | `1.3` |
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

- **Rows** use the shared row keys (see Shared vocabulary) wherever the playroutine's
  codec can express them: `{ctrl: 129, note: =68}`. A row no codec shape covers is
  written in the type's `form`: a `list` row holds the fields in declaration order, with
  trailing defaults left out (a GoatTracker pulse sweep `[32, 16]`); a `map` row holds
  the fields that differ from their defaults (`{ad: 1, sr: 136, gt: 4}`).
- **Programs** are the rows of a chained type, in the order they run, followed by
  `end` or `{loop: k}` (k is a row of the same program). A program is cut from the
  player's chained tables at every entry point. A tail two programs share is written
  in both, which is exact because rows are deterministic.
- **References.** A field that references another record holds a name. A program that
  only one instrument uses is written inline in that instrument instead
  (`ctrl: [{ctrl: 9, note: +0}, {ctrl: 65, note: +0}, end]`).
- **Fixed** record types, such as defMON's initial voice state, keep their indices and
  are defined in the first row: `vinit: {0: {...}, 1: {...}}`. They use the shared keys
  with their row meaning wherever one fits (`vinit` writes `ctrl`, `note`, `ad`, `sr`,
  `pw`, `wgl`, `af`, `ps`; `ginit` writes `resfilt`, `fv`, `cp`), and an omitted field
  is 0.

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command:arg[!command:arg]][*steps]
```

- `note` is a note name such as `C2`, `A#3` or `C10`, `-` for no note, `off` (gate off)
  or `on` (gate on). Names are real pitch: `C2` is 65.4 Hz in every song, whatever
  index the player's frequency table gives it (see `note_offset`).
- `.i3` names the instrument. For defMON it names a sidTAB program.
- `!run:@c1` is a named command with an argument: a number, `@name` for a command that
  references a record, or `none`. An event carries at most two commands, in the order the
  player reads them (for SID-Wizard: instrument column, note column, FX column). Command
  names are the ones below.
- `*3` makes the event last 3 steps; the steps after the first are empty.

## Shared vocabulary

Program rows and commands use one vocabulary in every song, in chip units.

| row key | meaning |
|---|---|
| `ctrl` | control register value |
| `note` | `+n` / `-n` relative to the event note, `=n` absolute (format numbering, C2 = 24); omitted: no change |
| `wait` | ticks before the row applies |
| `hold` | ticks the row lasts after applying; omitted: 1 |
| `ad`, `sr` | envelope registers |
| `pw` | the 12-bit pulse width written |
| `cut` | the 11-bit cutoff over `$D415`/`$D416` |
| `resfilt` | `$D417`, resonance and filter routing |
| `mode` | the `$D418` filter-mode bits, volume excluded |

| command | meaning |
|---|---|
| `!run:@x` | start program `x` in the slot its kind implies; `!run:none` stops it |
| `!run2:@x` | start program `x` in slot 2, for players with generic slots |
| `!ad:`, `!sr:`, `!ctrl:`, `!resfilt:`, `!vol:` | set the register |
| `!cut:n` | set the 11-bit cutoff |
| `!tempo:n`, `!vtempo:n` | n ticks per step, for all voices / this voice |
| `!funk:@s` | swing tempo pair |
| `!slideup:@s`, `!slidedown:@s`, `!porta:@s`, `!vib:@s` | pitch effects |
| `!mark:n` | a timing mark that changes no sound |

SID-Wizard's commands are its own: the effects have no identical shared form.

| command | meaning |
|---|---|
| `!pitchup:n`, `!pitchdown:n`, `!glide:n` | big FX $01-$03: slide, or slide to the note, at speed byte n |
| `!attack:n`, `!decay:n`, `!sustain:n`, `!release:n` | small FX $2x/$3x/$5x/$6x: one ADSR nibble |
| `!chord:@ch` | small FX $7x: select a chord |
| `!iattack`, `!idecay`, `!isustain`, `!irelease`, `!ichord` | the same small FX in the instrument column, which the player times differently |
| `!legato:0` | instrument column $3F: the note slides there without a new start |
| `!notevib:n` | note column $60-$77: vibrato with amplitude n, ending a slide |
| `!noteglide:0` | note column $78: the next note slides at the default speed |

A shared key means the same wherever it appears: in program rows, in initial-state
records, and in instruments, where `ctrl` names the program run on the control register
(GoatTracker's `ctrl: [...]` or `ctrl: c1`). A row or command a player can't express is
rejected on reading, naming the key or command and the player; the writer refuses to
write a value that would not read back (a defMON pulse cell over `$0FFF`). Player-specific leftovers keep their v1 form:

- **GoatTracker 2:** pulse and filter sweep rows and wavetable command rows (list rows),
  and the instrument keys.
- **defMON:** the sidTAB columns `wgl`, `af`, `ps`, `re`, `fv`, `cp`, `acid`.
- **SID-Wizard:** WF rows that hold a row (`w` below $10), play a chord (`a` $7F) or set a
  detune (list rows `[w, a, d]`), pulse and filter sweeps and keyboard-tracked rows (list
  rows), a filter row's resonance `res`, and the instrument keys.

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
note_offset: 0                            # frequency-table index of format note 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
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
| `codec` | shapes writing rows as shared keys (see Codecs) |
| `end` | raw fields set on a program's last row when it is followed by `end` |
| any other key | a template: an action list run for a row |

A template reads the row it runs for as `row.<field>`. `run: {record: r, index: i,
field: f}` runs record type `r`'s template `f` for row `i` when that row exists.
GoatTracker's control-program rows, for example, decode their bytes in the template:

```yaml
ctrltab:
  prefix: c
  none: 0
  chain: next
  fields: [w, n, next]
  do:
    - if: "row.w >= 0xF0"
      then: [{set: {arg: row.n}}, {run: {record: wavecmd, index: "row.w & 0x0F", field: do}}, ...]
    ...
```

### Codecs

A codec shape has a `when` over the raw fields, `keys` (`{key: {value, omit, abs}}`:
the shared key's value from the raw fields, when to leave it out, and for `note` whether
it is absolute), `raw` (each raw field from the written keys, with `has_<key>` and
`note_abs` defined) and `keep` (raw fields written under their own names; a kept reference is written
as a name). The writer uses the first shape whose `when` holds and refuses a row that
would not read back; a `map` row no shape fits is an error. The reader accepts a
written row only if encoding its inverse gives the row again. A command entry has a `name`, an optional
`ref`, and for a plain argument optional `when`, `arg` (raw to written) and `raw`
(written to raw), checked the same way.

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
| `sidwizard` | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row | the decoded module |

**GoatTracker 2.** The playroutine counts ticks per row itself, and `fetch` runs on its
gate-timer lookahead. Instrument 1 is pinned because every voice starts on it. A
filter parameter row carries the following cutoff row's value as `cut`, because the
player reads both in one tick.

**defMON.** The arranger and pattern counters are lifted as timing. Each song step
becomes one phrase per voice, all of the same length in ticks, with each event on the
tick the replay applies it. The playroutine fetches every tick and maps the event
fields as follows:

- `.t3` is the sidTAB program sidcall layer A starts;
- `!run2:@t5` arms layer B;
- the note is a GATE_N note.

sidTAB rows are maps of the columns present, applied in the replay's bitmap-walk
order: `dl` is `hold` (a frozen row ends its program), `wgh` is `ctrl`, `tr` is
`note`, and the other columns keep their names. Note names are offset by 24 indices:
defMON index 48 is `C2`. The replay probes `$D41B` at init to tell the SID models apart; the result
becomes the playroutine constants `floor` and `dbl`. The ghost masks pulse-width high
to a nibble, as the reference grid does.

**SID-Wizard.** The playroutine runs each voice's tempo counter, reads a row at TICK_0,
applies it at TICK_2 and writes the next instrument's hard-restart ADSR on the ticks
between, ticking the voices in the player's reverse order. An instrument is its header
(`flags`, `hrad`, `hrsr`, `ad`, `sr`, `vibrato`, `vibdelay`, `arpspeed`, `chord`,
`octave`, `firstctrl`) and its WF/arp, pulse and filter programs (`ctrl`, `pulse`,
`filter`). WF rows use `ctrl` and `note`; pulse set rows `pw`; filter set rows `mode`,
`cut` and `res`; chords are programs of `note` offsets that loop or end. Note names are
offset by one index: SID-Wizard index 49 is `C4`. Before a voice's first note the player
walks instrument 1's header as WF rows; the lifter requires each voice's first note to
select an instrument, so the walk reads only its first two rows. Table jumps, orderlist
transposes, tempo and volume commands, multispeed, other player builds and the effects
not listed above are not covered yet; the lifter names what it meets.
