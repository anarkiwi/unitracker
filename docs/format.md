# unitracker song format (v2)

A unitracker song is one YAML document. A backend lifts a native tune into it
(`unitracker-lift` reads GoatTracker 2 and defMON), and the generic player
(`unitracker-play`) plays it on an emulated SID. Songs from every player use one
vocabulary: register-named keys, note and timing keys, named commands, and one effect
form, the **bounded accumulator spec**. A short header comes first, then one timeline
in which every instrument, program, spec and phrase is defined in the row where it is
first used, so any prefix of a document is a playable partial song.

A song's **playroutine** (from the library in `src/unitracker/playroutines/`)
sequences: tempo, note fetch and lookahead, program stepping, hard restart and how
lanes combine into registers. Effects are specs the generic player runs.

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 2
playroutine: {ref: goattracker2, constants: {deftempo: 11}}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
hints: {ticks_per_step: 16, phrase_steps: 48}
song:
  - def:
      spec:
        v1: {step: 16, for: 32}
      ins:
        i1: {ad: 1, sr: 136, ctrlprog: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: =68},
            {ctrl: 65, note: +0}, end], pwprog: [{pw: 1024}, {pw: '@v1'}, {pw: {step: -16, for: 64}},
            {pw: '@v1'}, {loop: 1}], filtprog: [{filtmode: 16, resfilt: 193, cut: 128}, end], gateoff: 4,
          firstctrl: 9}
        ...
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, ...]
        p3: [off!tempo:16*48]
    play: [p1, p2, p3]
  - def:
      spec:
        v2: {step: 32, reset: command}
        v3: {step: 32, toward: note, width: 16}
        v4: {step: 32, flip: [3, 6]}
      ...
      phrase:
        p6: [off*3, A#3.i4!freq:@v2, C4!freq:@v3, D4, D#4*2, ..., F4!freq:@v4, ...]
    play: [p4, p5, p6]
loop: 0
```

`!freq:@v2` slides the pitch up 32 units a tick; `@v3` glides to the note; `@v4` is a
vibrato of depth 32 that turns after 3 ticks, then every 6.

## Document

Top-level keys, in this order:

| key | content |
|---|---|
| `unitracker` | `2` |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (`--embed`) |
| `meta` | `title`, `author`, `released` |
| `hints` | derived facts, such as `ticks_per_step` and `phrase_steps`; the player ignores them |
| `clock`, `voices`, `registers` | only where they differ from the playroutine's `defaults`; `registers` is `{index: value}` |
| `song` | the timeline: a list of rows |
| `loop` | the row the song loops to, or `~` to end |

`playroutine.constants` lists only the constants that differ from the library's, and
`playroutine.tables` patches its tables (`{index: value}`, or a full list when the
length differs). The patch holds only entries the song can read: the writer plays the
song, recording every table read, until its complete state recurs; an entry never read
cannot change the output, so the library default stands in for it.

## Timeline

Each row is `{def: {...}, play: [entry per voice]}`; `def` is present only when
something is first used in that row. An entry is `p3` (start phrase `p3`), `p3+5` or
`p3-2` (transposed), `end` (the voice stops) or `~` (still playing). A row starts at the
earliest end of the voices' current phrases; every voice whose phrase ends there must
have an entry, and every other voice must be `~`. Voice loops of different lengths are
unrolled until all voices are back in the same state together; `loop` is that row.

## Definitions

`def` maps a definition kind to `{name: value}`. Names are a prefix and a number in
first-use order. The kinds are `ins` (instruments), the program kinds `ctrlprog`,
`pwprog`, `filtprog` and `prog`, the fixed initial-state kinds `voiceinit` and
`globalinit`, `spec`, and `phrase`.

- **Rows** are mappings of shared keys (below), written in the record type's key order.
- **Programs** are rows in the order they run, then `end` or `{loop: k}` (k is a row of
  the same program). Programs are cut from the player's chained tables at every entry
  point; a tail two programs share is written in both.
- **References** are `'@name'`. A program only one instrument uses is written inline in
  it. `off` is the empty reference.
- **Specs** used more than once are defined under `spec` (`v1`, ...) and referenced as
  `'@v1'`; a spec used once is written inline.

## The shared vocabulary

| key | value | meaning |
|---|---|---|
| `ctrl` | register | $D404: waveform, gate, sync, ring |
| `ad`, `sr` | register | $D405 / $D406 |
| `pw` | 12-bit width, or a spec | the pulse width actually written |
| `freq` | spec | the voice frequency (absolute) |
| `bend` | spec, live value, `off` | an offset added to the note's frequency |
| `cut` | register units, or a spec | the cutoff high byte; fractional (1/256) where the player keeps a finer accumulator |
| `resfilt` | register | $D417: resonance and filter routing |
| `modevol` | register | $D418: filter mode and volume |
| `vol`, `filtmode` | register parts | $D418's volume (0-15) and mode bits ($10-$70) |
| `ctrlxor` | register part | an XOR mask applied to `ctrl` when written |
| `cutbias` | register part | an offset added to the cutoff output |
| `filtvoice` | register part | 0 removes this voice from the filter; with bit 3 set, keeps the routing, ORs the value and routes this voice |
| `note` | `+n`, `-n`, `=n` | a relative note, or an absolute one in the shared numbering |
| `notebase` | `=n` | the note relative notes count from |
| `wait` | ticks | the row applies after this many ticks |
| `hold` | ticks | the row lasts this many ticks |
| `ctrlprog`, `pwprog`, `filtprog` | reference | the program driving ctrl and pitch, the pulse width, the filter |
| `layer1`, `layer2`, `layer1wait`, `layer2wait` | reference, ticks | two program layers running at once, and their pending waits |
| `delay` | ticks | before an instrument's own `freq` spec starts |
| `gateoff` | ticks | before the next note, the gate goes off |
| `keepgate`, `nohr` | flag | no gate-off before a note; no hard-restart ADSR |
| `firstctrl`, `firstgate` | register, flag | the control value on a note's first tick; or only its gate |

A target key (`pw`, `freq`, `bend`, `cut`) given a number **sets** the lane, given a
spec **starts an accumulator** on it, given `off` stops it, and given an expression
string (only `bend`) drives it **live**, re-evaluated each tick.

**Note names are real pitch.** Event notes and `=n` notes use the shared numbering, in
which index 24 is C2 (65.4 Hz). Each playroutine declares `note_offset`, the shift from
its own note index (GoatTracker 0, defMON -24). Names may have negative octaves (`B-1`).

## Specs: the bounded accumulator

```yaml
{from: v, step: s | up: u, down: d, dir: up|down, carry_from: target,
 flip: [first, then, ...], toward: note, every: n, for: n,
 lo: a, at_lo: {...}, hi: b, at_hi: {...}, width: bits, reset: row|command}
```

Numbers are integers or fractions `a/b` in the target's units. `step`, `up`, `down`,
`toward` and `clamp` may be expressions using `semi` (the player's semitone interval at
the voice's note), `semi(k)` (k semitones above), `note` (the note's frequency) and `v`
(the value, in `clamp`). Each tick the playroutine advances a lane, the accumulator
updates once:

```
step  = up or down by direction | step, negated while a flip or reverse runs it backwards
step += the carry of carry_from's target, if given
new   = old + step, wrapped to width bits if given (bounds see the wrapped value)
if old <= hi < new: at_hi       if old >= lo > new: at_lo
at_*: default clamps to the bound; {clamp: x} sets x; {reverse} turns the direction;
      {wrap} continues from the other bound; {stop} stops after this update
flip: the direction turns after first, then after each later period (the last repeats)
toward: move |step| toward the target; passing it lands on it and counts as reaching it
        (landing exactly on it does not); with no step it lands at once, every tick
every: update on every nth tick only        for: stop after n ticks (0 never finishes)
```

Starting a spec sets `from` if given, restarts `for` and `every`, and keeps the lane's
flip phase unless the trigger that started it is in `reset` (`row` or `command`). A
spec of only `from` sets the value and stops the lane. A constant `step` sets the
lane's direction to its sign; `up`/`down` without `dir` keep the lane's direction.
`width`, when given, also defines the carry out other lanes read through `carry_from`.

Two parameters beyond step, bounds, flip, every, width, toward, reset and carry_from
exist because exactness needs them: `from` (defMON sets a width and starts or stops a
sweep in one row, and resets its ACID accumulator while keeping the slide direction)
and `dir` (defMON's pulse sweep starts in either direction with asymmetric steps).

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command[:arg]][*steps]
```

`note` is a note name, `-` for no note, `off` (gate off) or `on`. `.i3` names the
instrument (for defMON, the program layer 1 starts). `*3` makes the event last 3 steps.
A command's argument is an integer, `a,b`, `@name`, `off`, or a spec (an atom with an
inline spec is quoted). The commands:

| command | argument | meaning |
|---|---|---|
| `freq` | spec | start an accumulator on the frequency |
| `ad`, `sr`, `ctrl`, `cut`, `resfilt`, `vol` | value | set a register (or part) |
| `tempo`, `vtempo` | ticks per step | all voices / this voice; 0 and 1 select the funk tempo's phase |
| `funk` | `a,b` or none | alternate two tempos; without an argument, restart the alternation |
| `ctrlprog`, `pwprog`, `filtprog`, `layer2` | `@name` or `off` | switch a program |

## Canonical form

Names are given in first-use order; unreachable records and rows are dropped;
phrases equal up to a transpose are merged; runs of empty steps become `*steps`;
no-op commands are dropped. Writing what the reader returns gives the same bytes.

## The playroutine

```yaml
name: str
note_offset: int
constants: {name: int}
defaults: {clock: {...}, voices: 3, registers: [25 ints]}
tables: {name: [ints]}                   # player-build data; out-of-range reads give 0
instrument: {type: ins, base: 0}         # the record type .name refers to
lanes: {voice: {cell: {scale: n}}, global: {...}}   # accumulator targets; scale: units per 1
carry: {target: cell}                    # where a target's carry is kept
pitch: {note: expr, semi: {args: [k], expr: ...}}   # what spec expressions mean here
specs: {target: [params]}                # the spec parameters the player can express
spec_rules: [{check: expr, error: str}]  # further limits on specs
commands: {name: {code: int, arg: int|pair|pair?|spec|ref type}}
record_types: {name: {...}}
functions, global, voice, records, procs, init, tick, ghost   # as below
```

### Record types

| key | meaning |
|---|---|
| `keys` | `{key: kind}`: `int`, `note`, `abs`, `flag`, `ref type`, or `value forms` (forms of `set,spec,off,live`) |
| `prefix`, `chain`, `none`, `first`, `max`, `pin`, `fixed`, `defaults` | naming, programs, the "no row" index, allocation, fixed indices, defaults |
| `rules` | `[{check, error}]`: expressions over the row (`has(k)`, `count()`, `isspec(k)`, `spec.param`) the reader enforces |
| any other key | a template: an action list run for a row, which it reads as `row.<key>` |

The reader rejects a row, spec or command the player cannot express, naming the key
and the player.

### Actions

| action | meaning |
|---|---|
| `set: {cell: expr}` | assign, wrapped to the cell's width; assigning a lane sets its value |
| `if`/`then`/`else`, `call` (`with`), `run`, `for_voices`, `fetch`, `return` | control flow, procs, record templates, the cursor |
| `apply: {lane: row.key}` | give a lane a record value: set, spec (start), `off` or live; `once: true` runs a spec's single update without replacing the running one; `by: row` or `command` |
| `start: {lane: expr}` | start the spec whose id the expression gives (0 stops); `by` defaults to `command` |
| `advance: lane` | one tick of the lane's accumulator or live value |
| `stop: lane`, `restart: lane` | stop it; zero its flip phase |

Expressions are Python syntax restricted to integers, cell and constant names,
`table[i]`, `record[i].field`, `row.field`, arithmetic, comparisons and `a if c else b`,
plus `running(lane)`, `on(lane)`, `hit(lane)` (it reached a bound or target this tick),
`has(record[i].key)` and `towards(spec id)`.

### The cursor, the ghost and the flush

`fetch` reads a voice's next step into `ev_kind` (0 empty, 1 note, 2 off, 3 on),
`ev_note`, `ev_instr`, `ev_cmd` (the command's code), `ev_arg`, `ev_arg2`, `ev_argn`
(the number of argument parts; a spec argument is its id), `at_phrase_start` and
`ended`. Each tick the player runs `init` or `tick`, evaluates the ghost, and flushes the
registers that changed in GoatTracker 2's buffered order:

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

## Backends

**GoatTracker 2.** Each speedtable effect becomes a spec from `player.s`'s arithmetic:
a slide is `{step: spd, reset: command}`; a calculated speed is `semi>>r`; tone
portamento is `{step: spd, toward: note, width: 16}` (speed 0 ties at once); vibrato
with compare value c turns after m = c // 2 + 1 ticks and then every 2m ticks (2m + 1
when c is odd): `{step: depth, flip: [m, 2m(+1)]}`; c >= $7E never turns. Vibrato
phase resets when a note sets the pitch, a portamento arrives, or a slide command
starts. Wavetable command rows carry their one key (`{freq: spec with for: 1}`, `{ad}`,
...). Pulse and filter modulation rows are `{step: s, for: t}`. The lifter raises for
builds this playroutine does not cover (SIMPLEPULSE, full ghosting, init-routine SID
writes, instrument-vibrato-only), for wavetable commands with no shared form, and for
vibratos on different flip schedules that could share a phase.

**defMON.** The arranger and pattern counters are lifted as timing: each song step is
one phrase per voice. sidTAB rows become `prog` rows in the replay's bitmap-walk order.
The pulse sweep is `{up: s, down: {step: -(s+1), carry_from: freq}, dir, lo: 0, at_lo:
{clamp: 1, reverse}, hi: 4095, at_hi: {clamp: 4088, reverse}}`; slide modes $80+ are
`bend: {step: ±rate}`, modes 1-$7F are the live `bend: semi(k)`, mode 0 is `bend: off`;
ACID commands are `cut` specs on the 1/256-scaled accumulator, `{step, width: 8, hi:
32767/256, at_hi: {clamp: floor + v % 1}}`. The replay probes `$D41B` at init; the
results become the constants `floor` and `dbl`.

## The internal representation

`unitracker.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records holding shared keys (`noteabs` for an absolute
note) and a `next` field per program row, per-voice tracks of plays, phrases of event
dicts whose commands are `[name, arg]`, and the full playroutine and tables. Lifters
produce this representation and `songio.dumps` writes it.
