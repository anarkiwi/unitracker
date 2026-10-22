# Porting a playroutine

A port brings another C64 playroutine (a tracker's replay) into unitracker: its tunes lift to the
generic document of [unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar), verified
on [unitracker-player](https://github.com/anarkiwi/unitracker-player) against the native replay's
register grid. This guide assumes you know 6502 and the target player. The document is
[format/document.md](format/document.md); the modulator machinery is [modfit.md](modulation/modfit.md), the optimiser
[optimise.md](optimise.md), and each existing port's support and limits
[playroutines/](playroutines/).

In a worktree the venv's editable install points at the main checkout, so every command runs
with `PYTHONPATH=src`. `TUNE.sid` is the port's reference tune, `<name>` the playroutine's name.

## Tools

Every check a port needs already exists and is player-generic: a port adds a playroutine, a
lifter and its registration, and the tools pick it up through `backends.py`. Extend a tool rather
than writing a port-specific copy; the README's *Triage* table indexes the same tools by question.

| need | tool |
|---|---|
| the native replay's register grid | `pysidtracker.find_format(raw).play(raw, subtune).render_grid(n)`; in tests, `conftest.oracle(raw, frames)` (the 6502 oracle, cached) |
| the first differing `(tick, register)` of the lifter's song on its VM | `pysidtracker.gridcmp.first_mismatch(ours, theirs, starts, tol=pr.get("tolerance"))`; in tests, `conftest.vm_mismatch(song, theirs)` |
| whether the generic document plays the native grid on unitracker-player | `unitracker-lift TUNE.sid -o out/porting/doc.yaml` (exits 0 with `verified` on its last line); in code `backends.verify(text, raw, subtune)` |
| where the generic document differs, by register and value (exit 0 on agreement, `--json` the machine-readable diff) | `tools/corpus.py grid out/porting/doc.yaml TUNE.sid --ticks 3007 [--context N] [--json PATH]` |
| why a document or the lifter's song diverges: its tick beside a document's, the VM's, the native player's (traced in Python) or the raw tune's 6502 on jennings, each with the diverging value's dataflow | `verify.explain(verdict, subject, (ours, theirs), against).text()` (pysidtracker's `whydiff`); `tools/corpus.py grid DOC TUNE.sid --explain native\|6502\|vm` |
| the ticks the native unit holds each expression on, by the generic modulators that play it | `tools/modulators.py firings TUNE.sid --cell pw:0 --ticks A:B` |
| the verify verdict and forbidden native names for every reference tune | `tools/corpus.py check --out OUT --forbid NAME ...` (add the tune to `tools/shared.py`'s `REFERENCES`) |
| a modulator's occurrences and derived expressions | `tools/modulators.py occurrences TUNE.sid --unit U --cells c1,c2` |
| proofs, closed forms, command composition, keyword signatures | `unitracker-modfit {exprs,command,keywords,fit,poly,generic,sweeps,orbit}` ([modfit.md](modulation/modfit.md)) |
| the size of the generic document | `optimise.tokens(lift.document)` (its `cl100k_base` token count) |
| how many tunes of the player lift, by failure cause | `pysidtracker.survey` with `corpus:render` (README) |
| a reference tune in the tests | `conftest.hvsc_tune(name, "MUSICIANS/...", doc)` |
| a one-feature tune in the tests | `conftest.gt_tune`, `sw_fx`, `sw_table_byte` (patch a native module), `mini_song`, `table` (an IR song) |
| iterating a playroutine's command variants | `playroutine.codec.command_variants(spec)` |

## 1. What a port is

| part | file | content |
|---|---|---|
| playroutine | `src/unitracker/playroutines/<name>.yaml` | the replay transcribed as cells, procs, records and a ghost ([The playroutine](playroutines/schema.md)) |
| record types and codecs | the playroutine's `record_types` and `commands` | the native table layouts, and each row kind and command code as shared keys and back, rejecting what the player cannot play ([Record types](playroutines/schema.md#record-types), [Codecs](playroutines/schema.md#codecs)) |
| modulator units | the playroutine's `modulators` | the cells the song drives by expressions ([Modulator declarations](playroutines/schema.md#modulator-declarations)) |
| lifter | `src/unitracker/lift/<name>.py` | a `Lift` subclass: native module data to the lifter's song ([Lifting](playroutines/lifting.md)) |
| backend entry | `src/unitracker/lift/backends.py` | detection, and dispatch to the lifter and the native player |
| reference tune | `tests/conftest.py` | an HVSC tune fetched into `.tunecache/` and checked against the native replay |

The native replay comes from a Python package built on `pysidtracker` (`pygoattracker`,
`pydefmon`, `pysidwizard`) that decodes the module and runs the replay as a
`pysidtracker.MemPlayer`; its `render_grid(n)` is the reference register grid. A port needs one,
as a dependency in `pyproject.toml`, that registers a `pysidtracker` format (a `SidFormat` on the
`pysidtracker.formats` entry-point group) whose parser recognises the tune.

Start from the existing port closest to the target player:

| port | player | read it for |
|---|---|---|
| [`goattracker2`](playroutines/goattracker2.md) | instruments pointing into tables whose rows are waveforms, notes and commands; pattern commands; its own tempo counter | command dispatch through playroutine `records` (`tick0`, `cmd`, `tickn`, `wavecmd`), wavetable command rows written as the `commands` they run, the gate-timer lookahead |
| [`sidwizard`](playroutines/sidwizard.md) | an instrument header plus WF/arp, pulse and filter programs; a pattern row with an instrument column and an FX column | header bytes as instrument keys, two event slots, envelope nibble commands, `lifetime` flags |
| [`defmon`](playroutines/defmon.md) | no native instrument: sidTAB programs armed per step on two layers; one step per tick | `instrument: event` with the notes' restarts (`event: {restart}`), `fixed` init records, a `global` unit |

**Files:** none yet. **Verify** the toolchain and the existing ports:
`PYTHONPATH=src .venv/bin/python tools/corpus.py check --out out/porting` prints a `verified`
row for each reference tune.

## 2. Transcribing the replay

The generic player has no player-specific code: every cell, branch and table the replay's output
depends on is in the YAML. The schema is [The playroutine](playroutines/schema.md); the action
language [Actions](playroutines/schema.md#actions), [Cells and
expressions](playroutines/schema.md#cells-and-expressions) and [The bounded
accumulator](playroutines/schema.md#the-bounded-accumulator). Transcribe the replay in its own
order:

| replay | playroutine |
|---|---|
| build constants | `constants`, which the lifter sets per tune; `defaults` (clock, voices, tempo) where they are not PAL, 3 and none |
| the initial tempo | the song's `tempo`, which the lifter sets per tune |
| frequency and other lookup tables | `tables` (the document writes those its expressions read, see [Document](format/document.md)); `note_offset` |
| zero-page and self-modified operands, per voice or shared | `voice` and `global` cells, each with the `width` of its 6502 location; a scratch value has no width |
| jump tables (command dispatch) | playroutine-owned `records`, run with `run: {record, index, field}` |
| module tables (instruments, wave, pulse, filter tables) | `record_types` and their templates (section 3), run with `run: {record, index, template}` |
| subroutines | `procs`, run with `call` and `with` |
| the pattern's instrument column | `instrument: {type, base}`, the record type `.name` refers to |
| the init call | `init`, global scope, run in place of `tick` on the first tick: it includes the play call when the native player plays on its init frame (`defmon`'s ends `call: play`) |
| the play call | `tick` |
| `STA $D400`-`$D418` | the `ghost`: one expression per register over the cells |

Assignment wraps to the cell's width and arithmetic is unbounded, so each carry and wrap the
6502 makes comes from a width or an explicit expression (`defmon`'s 17-bit `acc`, its carry in
bit 16). Name each proc and comment it with the replay address it transcribes.

**Steps and ticks.** A step is what one `fetch` reads, and the playroutine decides when it
fetches. Two mappings are in use:

| mapping | ports | how |
|---|---|---|
| one step per pattern row | `goattracker2`, `sidwizard` | the replay's tempo counter is cells; the playroutine fetches on the tick the replay reads its next row (GoatTracker reads it the gate timer ahead) |
| one step per tick | `defmon` | the lifter simulates the arranger and pattern counters and puts each event on the tick the replay applies it; the playroutine fetches every tick |

Use one step per row when tempo is cell state the song's commands change (tempo commands are
then commands like any other); use one step per tick when timing comes from counters the format
has no command for.

**The cursor.** `fetch` sets `ev_kind`, `ev_note`, `ev_instr`, the two command slots
`ev_cmd`/`ev_arg` and `ev_cmd2`/`ev_arg2`, `at_phrase_start` and `ended` ([The
cursor](playroutines/schema.md#the-cursor)). Map each pattern column onto them; the lifter writes
events in the same form (section 6). An event carries at most two slot commands, one per slot.

**The ghost and the canonical flush.** The ghost gives each register after the tick, and the
player flushes the changed registers in one canonical order (*Ticks, the ghost and the canonical
flush*). The replay's write order does not matter: the grid is the state after each tick. A
replay that writes registers from values latched on the previous call transcribes the latch as
cells (`defmon`'s `latch` proc and `o_*` cells).

**The `onset` cell and the zeroed SID.** The player starts from a zeroed SID and carries none of the
native init writes ([Checking against the native
player](playroutines/schema.md#checking-against-the-native-player)). Declare a 1-bit voice cell named
`onset` and set it on the tick the voice's registers first hold its first note. The check
compares each voice from its onset and `$D415`-`$D418` from the earliest onset. Set it where the
replay writes the note's frequency, which need not be where the event is read (`goattracker2` sets
it in `wnote`, when a wavetable row first sets the frequency).

**Verify before converting.** With a first lifter (section 6) returning `expressions=False`
(native modulations, no conversion), compare the transcription with the native replay:

```sh
PYTHONPATH=src .venv/bin/python -c '
import sys, numpy as np
from pathlib import Path
from pysidtracker import find_format
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer
raw = Path(sys.argv[1]).read_bytes()
theirs = np.asarray(find_format(raw).play(raw).render_grid(3007), dtype=np.uint8)
print(SongPlayer(backends.lift(raw, expressions=False)).mismatch(theirs))
' TUNE.sid
```

It prints `None`, or the first `(tick, register)` that differs. Once the lifter writes a generic
document (section 6), `tools/corpus.py grid` prints where it differs — each register, the tick
runs, and the document's and the native's values over them — and both grids around the first
difference:

```sh
PYTHONPATH=src .venv/bin/python tools/corpus.py grid out/porting/doc.yaml TUNE.sid --ticks 100 --context 2
```

It exits 1 on a difference; `--json PATH` (or stdout on `--json`) writes the machine-readable
diff, so a divergence is a parseable list of registers, runs and values rather than a one-line
verdict.

`--explain native|6502|vm` adds why it differs: pysidtracker's `whydiff` traces the document's
tick on unitracker-player line by line beside the native player's (traced in Python), the tune's
play call on jennings, or the lifter's song on the VM (judged at its own onset), each with the
backward slice of the diverging register, e.g. a `!ad` command's value through `event.ops` and
`setattr(voice, key, ...)` to the register. In code, `verify.explain(verdict, subject, (ours,
theirs), against)` explains any pair: `subject` and `against` each a document's text, a song (the
VM) or a native player factory, or `against` the raw tune (`subtune=`); `verify.side` builds one
side and `verify.DocumentPlayer` is unitracker-player as a whydiff player (`play_frame`, `regs`).

**Files:** `src/unitracker/playroutines/<name>.yaml`. **Verify:** the snippet above prints `None`.

## 3. Record types and codecs

Declare each native table as a record type ([Record types](playroutines/schema.md#record-types)):

| native table | keys |
|---|---|
| instruments | `form: map`, `fields` with their defaults, `refs` to the programs they start, and `pin` for an index the replay reads directly (GoatTracker's instrument 1) |
| a table of programs with a next pointer or jump | a `next` field (or `chain`, the next-row field), `stop` (a row after which the program ends, `defmon`'s frozen row), `end` (raw fields set on a program's last row) |
| rows whose effect depends on their kind | templates (any other key, such as `do` and `each`), reading `row.<field>` |
| module state carried through init | `fixed: true` (`defmon`'s `vinit`, `ginit`) |
| a modulated program | `expr: x`, the field holding a row's expression (its `refs` entry is implied) |
| raw fields with no written form | `unwritten: [field, ...]` |

`writes_as` names the record type the table is written as (`ins`, `ctrl`, `pw`, `filter`,
`restart`), and `prefix` its name prefix when that
is not the written name's first letter. `none`, the index meaning "no row", is 0 unless given
(`none: null` when every index is a row, as `defmon`'s sidTAB). The type written as `ins` is the
instrument an event names unless `instrument` says otherwise, and `note_offset` is 0 unless given.
A field for which 0 is meaningful goes in `optional`, read as
-1, "absent", when missing (the `restart` rows, `defmon`'s sidTAB columns); an optional field that is
a shared key needs no `keys` or `raw` entry of its own. A `hold` key needs no `omit` or
`has_hold` fallback: it is left out at 1 and reads as 1 when left out. Read a signed byte or word
with the builtins `s8` and `s16`.

**Codec shapes.** Each `codec` entry is one row kind: `when` over the raw fields, the constants and,
for a program row, its place (`first`, `last`); `keys` from raw fields to shared keys; `raw` back
([Codecs](playroutines/schema.md#codecs)). Write
every row kind onto the shared row keys ([Programs](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#programs)), each in its one
meaning in chip units; a player that stores the 11-bit cutoff as its top byte writes the `cut` key
as `{byte: f}` and the `cut` command as `{name: cut, byte: arg}`, and its `raw` reads `cut` as that
byte; a note byte (bit 7 absolute, the rest a signed relative note, one byte no note) is
`note: {byte: f, none: 0x80}`, with `abs: [lo, hi]` when fewer bytes are absolute, and needs no
`raw` entry. A row is accepted only when encoding its raw inverse gives the row again, so a
shape whose `keys` and `raw` disagree refuses its own rows. Command codes go in
`commands`: code to `{name, ref, when, arg, raw}` (or `byte` for `arg`), `{name, when, args, raw}` for several values,
`{name, ref, inline: true}` for a row written in place, or `{name, nibble, column}` for an envelope command; `column` gives the event column a command is
read into.

**No player's own vocabulary.** No key, command or record type is one player's own. What the
player cannot play has no shape, so the codec refuses it naming the key or command and the
player (`sidwizard: the player can't express !tempo:2`). Never add a key for one player: resolve
the native row at lift time into a form that has shared keys (section 6), or fail the lift naming
it. A shared form two players need and the generic document lacks is a change to
unitracker-grammar and the shared vocabulary (`ROW_KEYS` and `COMMAND_NAMES` in `playroutine/codec.py`), not
part of the port.

**The vocabulary and command-table tests.** `tests/test_format_codec.py` runs over every playroutine
in `playroutines/` (`PLAYERS`). `tests/test_playroutine_docs.py`'s
`test_the_documented_tables_are_the_playroutines` generates each playroutine's command table and
`ctrl` row key table from its YAML and checks them against `docs/playroutines/<name>.md`, so a new
playroutine needs that file, laid out as the existing ones; the failing assertion prints the
expected table to paste into it. `test_every_key_a_library_playroutine_uses_is_documented` checks
each key the YAML uses against `docs/playroutines/schema.md`. Add the player's rows, restarts,
refusals and commands to `ROWS`, `RESTARTS`, `REFUSALS`, `COMMAND_ROWS` and `COMMAND_REFUSALS`, and
its frequency table to the parameters of `test_note_offsets_follow_the_frequency_tables`.
`tests/test_reference.py`'s `test_the_players_share_only_the_shared_vocabulary` checks that the
lifted reference documents use only the shared vocabulary, no two players sharing a key outside
`ROW_KEYS`: add the reference tune to `TUNES`, its backend to `BACKEND` and its expected command set
to `COMMANDS`.

`unitracker-modfit keywords <name>` lists each command and record field with the shared key it is
written via and its signature against the other playroutines'
([Signatures](modulation/modfit.md#signatures)): a field with another player's effect under a
different key is a vocabulary error.

**Files:** the playroutine's `record_types` and `commands`; `tests/test_format_codec.py`;
`tests/test_playroutine_docs.py`; `tests/test_reference.py`; `docs/playroutines/<name>.md`
(*Records*, the two generated tables). **Verify:**

```sh
PYTHONPATH=src .venv/bin/python -m pytest -n 4 tests/test_format_codec.py tests/test_playroutine_docs.py tests/test_reference.py
PYTHONPATH=src .venv/bin/unitracker-modfit --out out/porting/keywords.txt keywords <name>
```

## 4. Modulators

A unit is a cell the song drives by an integer expression in `v`, `φ` and `N`, evaluated at each of
the unit's firings ([Expressions](modulation/modfit.md#expressions)). Declare one per native modulation the
lifter converts: a pitch effect (`key: freq`), a pulse sweep (`pw`) or a filter sweep (`cut`). The
unit keys are in [Modulator declarations](playroutines/schema.md#modulator-declarations):

| key | what it transcribes |
|---|---|
| `cell`, `scope` | the cell the replay modulates (`hi:lo[:k]` for a pair); `scope` is `global` for a global cell, which the voices share |
| `fire` | one firing, as proc names, `run` actions and functions: `v` is read before the first entry runs and the value stored after the last |
| `hooks`, `program` | the starts: a function (`at: {proc: p}`, `{record: r, field: f, index: i}` with `i` a row or a list of rows, or `{type: t, template: f}`) writing a `written` cell, changing the `changed` cells, or writing a `pointer` to a program that sets one of `program.fields`; with none of these, every call of the function is a start. Left out, they are the functions outside the firing that write the site's `index` (`program.pointer`); declare them only for a start that is not a pointer write. Never add a proc only to label a firing or a start: name the function the player already runs |
| `x` | the expression a start selects; 0 leaves the playroutine's own firing; by default the site's field |
| `label` | the cells grouping occurrences; by default the site's index, the program pointer |
| `site`, `retire`, `commands` | where the lifter writes the expression, the native fields it replaces, and the command codes `!freq` (the `commands` entry named `key` with `ref: x`) replaces |
| `chip` | the chip map, below |

A start the `site` does not cover is written as the event's `!freq`. When `x` reads the site's
own record field (GoatTracker's command unit falls back to the instrument's `freq` on a step
without a command), each step the start spans without a command gets the `!freq`; otherwise
(SID-Wizard's pitch unit, whose cell a commandless row leaves alone) only the step that starts
it does (`convert.falls_back`).

The top level of `modulators` gives what expressions read: `freq` (the word `F(k)` reads, or the
function that reads it),
`notes`, `transpose`, `legato` (the cell the `legato` command sets), and the named `tables` and
`functions`.

**The chip map.** `chip: {width, value, store}` gives the unit's value `v` from its cells (`value`,
`width` bits) and writes a value back (`store`, run only when the cells do not already give it).
`chip: {width, offset}` generates both: `v` is the cells less `offset`, a cell narrower than `width`
holding its top bits, and `store` the inverse. `v`
must be a bijective image of the cell: each cell state the firings reach gives its own `v`, and
`store` inverts `value`. A map that loses information (a clamp, a floor) makes two cell states one
`v`, and no recurrence over `v` is exact. Keep `value` a linear image of the cell, plus at most a
term that does not depend on the cell, wrapped to `width`, and move the rest into the ghost, where
the register is written: GoatTracker's cutoff is `filtercutoff << 3` (`chip: {width: 11}`),
SID-Wizard's pulse is `pwhi:pwlo` plus its keyboard-tracking term in the note (`offset` its
negation), and `defmon`'s `acid
B` is its whole accumulator, with the floor, the `dbl` shift and the offset in the ghost's `$D416`.
A value the replay carries from earlier playback becomes a constant of the start's expression, which
the lifter resolves ([Expressions](modulation/modfit.md#expressions)).

**Running `unitracker-modfit`.** The `exprs` report lifts the tune with native modulations, converts
it, and gives per unit and label the occurrences, distinct starts, each start's phase, value update,
closed form, proof and the invariant it is proved under, the chip map, and where each text is
written ([modfit.md](modulation/modfit.md#phase-and-value-expressions)). A start that does not prove
fails the lift, naming the unit, its program and the start ([Lifting](playroutines/lifting.md)).
`tools/modulators.py occurrences` lists the occurrences one per line with the start cells asked for, and
`unitracker-modfit command <name>` composes each command's setup and per-tick effect, which checks a
dispatch transcription before any tune uses it:

```sh
PYTHONPATH=src .venv/bin/unitracker-modfit --out out/porting/exprs.txt exprs TUNE.sid
PYTHONPATH=src .venv/bin/python tools/modulators.py occurrences TUNE.sid --unit "pulse A" --cells rowA
PYTHONPATH=src .venv/bin/unitracker-modfit command <name>
```

**Files:** the playroutine's `modulators`. **Verify:** `out/porting/exprs.txt` says, for the
tune, "the rewritten song plays the native song's register grid exactly over them", and every
start's proof is `proved`.

## 5. Hard restart, chords, filter, lifetime and relative control

Each has one shared form; map the player's version onto it in the codec and refuse the rest, as
the existing ports do.

| feature | shared form | declare | each player's codec constraints |
|---|---|---|---|
| hard restart | a `restart` program anchored to the note: lookahead rows with `hold`, `gate`, `ctrl`, `ad`, `sr`, then the note's first-tick row ([Hard restart](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#hard-restart)) | a `restart` record type with shapes on `first` and `last`; an instrument field `restart`, played by command `c` (`instrument: {event: {restart: c}}`) for a player whose notes arm it on the steps before them ([Instrument columns](playroutines/schema.md#instrument-columns)) | each playroutine file's *Hard restart* |
| chords and arpeggios | a `ctrl` program of relative-note rows, a different chord started with `!run` ([Programs](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#programs)) | `note: +n` in the wave or arp table's shapes; the lifter inlines a separate chord table into the program | sidwizard.md, *Control programs* |
| filter | `res`, `route` (a mask, or the edit `+ m`, `-`), `mode`, `cut`, and `!vol` ([Programs](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#programs)) | filter program shapes; `rel` on `route` for an edit | each playroutine file's *Filter* or *Records* |
| lifetime | `!pw`, `!filter` on the note ([Notes](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#notes)) | `lifetime: {pw: {field, flag}, filter: {field, flag}}` ([Lifetime](playroutines/schema.md#lifetime)) | each playroutine file's *Lifetime* |
| relative control | `ctrl: ^n`, the exclusive-or mask the voice's control output keeps ([Programs](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#programs)) | `eor` on the `ctrl` key | `defmon` only; the others refuse it |
| legato | `!legato` ([Notes](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#notes)) | `modulators.legato: {cell, command}` | each playroutine file's command table |
| tempo | the tempo commands and the initial `tempo` ([Time](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#time)) | `commands` entries with `when`, `arg`, `raw` | each playroutine file's command table; a tempo command no entry declares is refused by name |

The modules of `src/unitracker/document/events/` resolve the context these forms need by walking
each voice's plays: `lifetime.py` (the instrument a note plays), `envelope.py` (an
envelope nibble command as the byte it writes), and the event folds `fold.py`, `instrument.py` (the
record an event's columns arm) and `restart.py` (a restart run on the steps before its note), and
`column.py` refuses two commands on one event for a column that holds one. They read the
playroutine's `lifetime`, `commands` with `nibble` or `column`, `instrument: event` and `restart`
declarations, so a port declares them rather than codes them; each raises its own error
naming what has no written form.

**Files:** the playroutine's `restart` record type, `lifetime`, `restart`, `instrument`, codec
shapes and `commands`; the player's cases in `tests/test_format_events.py` (lifetime commands,
envelope nibbles, restarts, one-command columns) beside the existing players'. **Verify:**
`PYTHONPATH=src .venv/bin/python -m pytest -n 4 tests/test_format_events.py`.

## 6. The lifter

`src/unitracker/lift/<name>.py` subclasses `unitracker.lift.base.Lift`, as
`goattracker.GoatTrackerLift`, `sidwizard.SidWizardLift` and `defmon.DefmonLift` do, and raises
`base.LiftError`. The base runs the passes ([Lifting](playroutines/lifting.md)); the subclass
supplies the player:

| member | content |
|---|---|
| `playroutine` | the library name, `"<name>"` |
| `decode(raw, subtune)` | the native package's decoded module, kept as `self.tune` |
| `native()` | a fresh native player of the tune; state its passes share, read from it once, is a `functools.cached_property` (GoatTracker's `start`, the tempo cells after init; defMON's `replay`, the build located and its calls timed) |
| `check_build()` | raises `LiftError` naming the builds and features the playroutine does not cover |
| `structure()` | the lifter's song, below |
| `normalisations` | the ordered player-specific passes `(lift, song) -> song`, each proved by a replay |
| `convert(song)` | `modfit.convert.convert` by default; an override post-processes its song (GoatTracker's `snapped`) |

`structure()` returns:

| key | content |
|---|---|
| `unitracker` | `"ir"` |
| `meta` | `base.song_meta(header, "<name> subtune N")` |
| `clock`, `voices` | from the PSID header and the build |
| `tempo` | the initial tempo as written, `n` or `[a, b]`, for a playroutine that declares `tempo` |
| `tables` | the module's tables the playroutine reads (the frequency table) |
| `records` | `{record type: {index: {field: value}}}`, under the playroutine's own type names |
| `tracks` | per voice `{"plays": [{"phrase": name, "transpose": t, "repeat": n}], "loop": k}` |
| `phrases` | `{name: [event]}`; an event has `note` (the player's note index, `off` or `on`), `instrument`, `command: [code, arg]` or `commands: [[c, a], [c, a]]`, and `steps` (`vm.fold_empty`) |
| `playroutine` | `playroutine.library.library("<name>")` with `constants` set from the build |

`run()` checks the build, runs `structure` and the normalisations, then, with `expressions`,
`convert`, `canonical` and `verify`, which leaves the generic document in `document` and its
verdict on unitracker-player against the native grid in `verdict`; `passes()` names the stages in
order and `stages()` yields each one's song. Register it in `backends.py`'s `LIFTS` under the
name of the `pysidtracker` format that recognises the tune (`detect`, `pysidtracker.find_format`):
`lifter` dispatches to `<Name>Lift.from_sid(raw, subtune, expressions)`, and `lift` and `verify`
go through it; the native player is that format's (`find_format(raw).play(raw, subtune)`). The
CLI, the tools and the tests go through these.

**Resolved at lift time.** The generic document carries only what the composer's rows and definitions
imply, so the lifter resolves what the native data leaves implicit:

| what | example |
|---|---|
| the build | constants (`defmon`'s `floor` and `dbl`) and the initial tempo; `check_build` refuses builds and features the playroutine does not cover |
| carried modulator values | a slide's start frequency, a delay counter, an ACID direction: `convert` writes each as a constant of its start's expression |
| instrument context | the voice's instrument at each note: SID-Wizard's octave shift into the notes, its envelope bytes, a first note that selects no instrument |
| module state through init | `defmon`'s `vinit` and `ginit`; a cell the replay's init sets is refused, naming it |
| native rows with no shared form | rewritten to rows that have one when a replay proves it unheard (GoatTracker's `unpause` of `$F7` and `$FD`) |

**What fails loudly.** Raise `LiftError` naming the row and what is not covered
(`ctrltab row 12: wavetable command $F0 stops the playroutine: not lifted`, `voice 1's
first note selects no instrument`). Never fall back to an
approximation: acceptance is the exact grid, and an unproved modulation raises
`ConversionError`. List what the lifter refuses in `docs/playroutines/<name>.md`, *Not covered*.

**Files:** `src/unitracker/lift/<name>.py`, `src/unitracker/lift/backends.py`, `pyproject.toml` (the
native package, the description), `docs/playroutines/lifting.md` (its table),
`docs/playroutines/<name>.md` (*Lifting*, *Commands*, *Not covered*). **Verify:**

```sh
PYTHONPATH=src .venv/bin/unitracker-lift TUNE.sid -o out/porting/doc.yaml
PYTHONPATH=src .venv/bin/python tools/corpus.py grid out/porting/doc.yaml TUNE.sid
```

`unitracker-lift` exits 0 with `verified: 3007 ticks play the native grid` on its last line when
the generic document plays the native grid on unitracker-player; `corpus.py grid` names the
differing registers and values, and exits 1 on a difference.

## 7. Tests and acceptance

| test | file | shape |
|---|---|---|
| reference tune | `tests/conftest.py` | a session fixture `hvsc_tune(name, "MUSICIANS/.../Tune.sid", doc)` (local `$HVSC`, `.tunecache/`, or the mirror; skipped when unavailable); the path also goes in `tools/shared.py`'s `REFERENCES` |
| synthetic tunes | `tests/conftest.py` | a builder like `gt_tune`, returning `(decoded tune, native player)` for a one-feature tune, where the native package can build modules |
| conformance | `tests/test_reference.py` | the `references` fixture lifts once per session and renders the native grid over `REFERENCE_TICKS[name]` (the intro and two loops, in `tests/conftest.py`); the tests check a non-trivial grid (`len(np.unique(ref.native, axis=0))`) and the generic document's verify verdict; add the tune to `TUNES` |
| dispatch | `tests/test_reference.py` | `backends.detect` against `BACKEND[name]`; the CLI's lift equals the session's lift (a deterministic lift); the CLI's exit status and verdict line |
| vocabulary, command tables | `tests/test_reference.py`, `tests/test_format_codec.py`, `tests/test_playroutine_docs.py` | section 3 |
| lifter | `tests/test_lift_<name>.py` | each `LiftError`, with `pytest.raises(LiftError, match=...)`, as `tests/test_lift_goattracker.py`; the passes, as `tests/test_lift_base.py` |
| exprs report | `unitracker-modfit exprs` | section 4 |
| optimiser | `tests/conftest.py` `OPTIMISED` | the tune with a subset of passes that keeps the suite's time bounded |
| a song against a native grid | `tests/conftest.py` | `assert_plays(request, song, theirs, native)`, `native` a player factory or the raw tune: its failure, and a strict xfail's `-rx` line, carry `verify.explain`'s report; a document's verdict carries the grid diff and, given what it was compared against, its explanation (`grid_diff_of(request, verdict, text, against)`) |

**Acceptance:** `unitracker-lift TUNE.sid -o out/porting/doc.yaml` exits 0, and so does
`unitracker-lift TUNE.sid -o out/porting/doc.opt.yaml --optimise`, which runs every pass of the
[optimiser](optimise.md) on the lifter's song before verify; `tools/corpus.py check`, with the
native table and column names as `--forbid`, prints a `verified` row with no forbidden word;
coverage stays above 85% and the lint is clean. Run `pylint` without `-j`: the parallel run misses cross-module checks such as
W0621. Then run the HVSC survey over the player's tunes (README) to see which builds and
features the lifter still refuses.

**Files:** `tests/conftest.py`, `tests/test_reference.py`, `tests/test_lift_<name>.py`,
`tools/shared.py`. **Verify:**

```sh
PYTHONPATH=src .venv/bin/black --check src tests tools
PYTHONPATH=src .venv/bin/pylint src
PYTHONPATH=src .venv/bin/pylint --disable=missing-function-docstring tests tools
PYTHONPATH=src .venv/bin/python -m pytest -n 4 --cov=unitracker --cov-fail-under=85
```

## 8. Checklist

| # | step | files | command (each with `PYTHONPATH=src`) |
|---|---|---|---|
| 1 | pick the closest port; fetch the reference tunes | `tests/conftest.py` | `.venv/bin/python tools/corpus.py check --out out/porting` |
| 2 | transcribe the replay: cells, tables, procs, `init`, `tick`, ghost, `onset` | `playroutines/<name>.yaml` | the `expressions=False` snippet of section 2 prints `None` |
| 3 | a first lifter (a `Lift` subclass) and its registration | `lift/<name>.py`, `lift/backends.py`, `pyproject.toml` | the same snippet, through `backends.lift` |
| 4 | record types, codec shapes and commands on the shared keys | `playroutines/<name>.yaml` | `.venv/bin/unitracker-modfit keywords <name>` |
| 5 | hard restart, chords, filter, lifetime, relative control in their shared forms | `playroutines/<name>.yaml` | `.venv/bin/python -m pytest -n 4 tests/test_format_events.py` |
| 6 | modulator units and chip maps | `playroutines/<name>.yaml` | `.venv/bin/unitracker-modfit --out out/porting/exprs.txt exprs TUNE.sid` |
| 7 | lift with expressions; verify the generic document on unitracker-player | `<name>.py` | `.venv/bin/unitracker-lift TUNE.sid -o out/porting/doc.yaml` exits 0 |
| 8 | where the generic document first differs from the native grid, by register and value | | `.venv/bin/python tools/corpus.py grid out/porting/doc.yaml TUNE.sid --ticks 3007 --json out/porting/diff.json` |
| 9 | vocabulary and command tables; the playroutine's doc | `tests/test_format_codec.py`, `tests/test_reference.py`, `docs/playroutines/<name>.md`, `docs/playroutines/lifting.md` | `.venv/bin/python -m pytest -n 4 tests/test_format_codec.py tests/test_playroutine_docs.py tests/test_reference.py` |
| 10 | conformance, dispatch and lifter tests | `tests/` | `.venv/bin/python -m pytest -n 4 tests/test_reference.py tests/test_lift_<name>.py` |
| 11 | the optimiser on the lifted tune | `tests/conftest.py` | `.venv/bin/unitracker-lift TUNE.sid -o out/porting/doc.opt.yaml --optimise` exits 0 |
| 12 | verify verdicts and forbidden names | `tools/shared.py` | `.venv/bin/python tools/corpus.py check --out out/porting --forbid <native names>` |
| 13 | lint and the full suite | | the four commands of section 7 |
| 14 | the player's tunes over HVSC | | `pysidtracker.survey` with `corpus:render` (README) |
