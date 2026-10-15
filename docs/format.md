# unitracker song format (v0)

A unitracker song is one YAML document. A backend writes it by lifting a native tune
(`unitracker-gt` lifts GoatTracker 2), and the generic player (`unitracker-play`)
plays it on an emulated SID. The player has no effects or player knowledge of its
own. Everything a player does is described by the song's `playroutine`: integer cells,
bounded accumulators, records of actions, and handlers.

## Top level

```yaml
unitracker: 0
meta: {title: str, author: str, released: str, source: str}
clock: {cycles_per_tick: 19656, clock_hz: 985248}
voices: 3
registers: [25 ints]          # SID register file before the first tick
tables: {name: [ints]}        # integer arrays, indexed from 0; reads out of range give 0
records: {name: {index: {field: int | [actions]}}}
tracks: [track, ...]          # one per voice
phrases: {name: [event, ...]}
playroutine: {...}
```

A record's missing int field reads as 0, and a missing action-list field is empty.
Instruments are just records, conventionally named `ins`.

## Structure

```yaml
track:  {plays: [{phrase: name, transpose: 0, repeat: 1}, ...], loop: 0}   # loop: play index, or null to end
event:  {steps: 1, note: 24 | 'off' | 'on', instrument: 1, command: [code, arg]}
```

Every event field is optional. With no `note`, the event is a rest. An event lasts
`steps` steps: the event itself, then `steps - 1` empty events. `note` is a semitone
index; the play's `transpose` is added when the event is fetched. A `repeat` of n
plays the phrase n times.

The player keeps one cursor per voice. It does not decide when events are read. The
playroutine decides that with the `fetch` action, which reads the next step and sets
these voice pseudo-cells:

| cell | value |
|---|---|
| `ev_kind` | 0 empty, 1 note, 2 off, 3 on |
| `ev_note` | note + transpose |
| `ev_instr` | instrument, or 0 when absent |
| `ev_cmd`, `ev_arg` | command, or 0 and 0 when absent |
| `at_phrase_start` | 1 when the next `fetch` reads step 0 of a phrase |
| `ended` | 1 once a track with `loop: null` has run out |

## Playroutine

```yaml
playroutine:
  name: str
  constants: {name: int}
  functions: {name: {args: [a, b], expr: "..."}}   # inlined into expressions
  global: {cell: {width: 8, init: 0}}                # width omitted: unbounded
  voice:  {cell: {width: 8, init: 0}}                # one copy per voice
  records: {name: {index: {field: ...}}}             # playroutine-owned, e.g. command dispatch
  procs: {name: [actions]}
  init: [actions]                                    # first tick only, global scope
  tick: [actions]                                    # every later tick, global scope
  ghost:
    global: {0x15: expr, 0x16: expr, 0x17: expr, 0x18: expr}
    voice: {0: expr, 1: expr, ..., 6: expr}          # offsets within a voice
```

Every assignment wraps to the target cell's `width`, using two's complement for
negative values.

### Expressions

Expressions are Python expression syntax restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`;
- `record[i].field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`.

Arithmetic is unbounded; only assignment wraps.

### Actions

| action | meaning |
|---|---|
| `set: {cell: expr, ...}` | Assign in order, each wrapped to its cell's width. |
| `if: expr`, `then: [...]`, `else: [...]` | Conditional. |
| `call: proc`, `with: {cell: expr}` | Assign the `with` cells, then run the proc. |
| `run: {record: r, index: expr, field: f}` | Run the action list stored in a record field. |
| `for_voices: [...]` | Run the list once per voice, in voice order, with that voice's cells bound. |
| `fetch: true` | Advance this voice's cursor (see Structure). |
| `return: true` | Leave the innermost proc, record action list, `for_voices` body, or `init`/`tick`. |
| `accumulate: cell` | The bounded accumulator; see below. |

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

Slides, portamento, vibrato, pulse and filter sweeps, tempo counters and table
timers are all this one action, triggered from handlers.

## Ticks, the ghost and the canonical flush

Each tick, the player runs `init` (first tick) or `tick` (every later tick). Then it
evaluates the ghost, masks each value to 8 bits, and flushes the registers that
changed since the last flush, in the canonical order. That order is GoatTracker 2's
buffered order (`BUFFEREDWRITES`: filter step first, then `mt_loadregs`):

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

A playroutine never writes registers directly, so every song's output is canonical by
construction.
