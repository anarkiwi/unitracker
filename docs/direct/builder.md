# Tracer and shared builder (spike)

`unitracker.direct.defmon_trace` re-implements the defMON lifter as a format-specific tracer
whose format-neutral event stream (`unitracker.direct.trace`) `unitracker.direct.builder` turns
into a document. `unitracker.direct.defmon` stays as the reference implementation.

```sh
python -m unitracker.direct.defmon_trace TUNE.sid > tune.yaml
UNITRACKER_LIFTERS=defmon=defmon-trace unitracker-lift TUNE.sid   # cli.lift/verify, corpus.py
```

## The stream

| event | meaning |
|---|---|
| `Step(tick, key)` | a song step; the song loops to the first step whose `key` recurs |
| `Note(tick, voice, note, ins, legato)` | a note on |
| `Start(tick, voice, pos)` | a program starts from table position `pos` |
| `Command(tick, voice, key, value)` | an event command |

A tracer also gives its header parts (`head()`: meta, clock, slots, state, tables, code
templates, instruments), each voice's init event, `step(pos)`: the position after one tick and
the rows it fetches, and `bind(effect, voice)`: the code template bindings of an `Effect`.
Rows are document keys and `Effect(kind, params)` of the shared kinds `pitch`, `glide(rate,
down)`, `detune(interval)`, `pulse(set, width, speed)`, `cutoff(layer, set, offset, value,
step)`; a `run` of a `Run(kind, Program)` is a nested program the builder names.

## The builder

- walks each started position to a stop or a recurring position (the loop), each row held to
  the next fetching tick;
- drops an all-empty loop tail, trailing empty rows and empty rows (their ticks the row
  before's; an empty loop row's also the loop's last row's);
- drops a row key whose value every execution already holds: a dataflow over each program's
  rows and loop, its entry the join of its starts (a note's state at a note), joined at each row
  boundary with what a note during it, a command, its own pulse programs or another voice
  binding a shared slot may set; a key stays where a slot's `row.` event needs it;
- shares programs and phrases by content and names them in order of first use.

## Mismatches

`builder.explain` runs `pysidtracker.whydiff.explain_divergence` with the document played by
unitracker-player (`DocumentPlayer`, the register file `Player.regs`) beside the 6502.
