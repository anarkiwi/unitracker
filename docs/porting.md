# Porting a playroutine

A port brings another C64 playroutine (a tracker's replay) into unitracker: its tunes lift to
documents in the shared vocabulary and play on the generic player with the native replay's
register grid. This guide assumes you know 6502 and the target player. Terms and section names
in *italics* are those of [format.md](format.md); the modulator machinery is
[modfit.md](modfit.md), the optimiser [optimise.md](optimise.md) and its report
[orthogonality.md](orthogonality.md).

In a worktree the venv's editable install points at the main checkout, so every command runs
with `PYTHONPATH=src`. `TUNE.sid` is the port's reference tune, `<name>` the playroutine's name.

## 1. What a port is

| part | file | content |
|---|---|---|
| playroutine | `src/unitracker/playroutines/<name>.yaml` | the replay transcribed as cells, procs, records and a ghost (*The playroutine*) |
| record types and codecs | the playroutine's `record_types` and `commands` | the native table layouts, and each row kind and command code as shared keys and back, rejecting what the player cannot play (*Record types*, *Codecs*) |
| modulator units | the playroutine's `modulators` | the cells the song drives by expressions (*Modulator declarations*) |
| lifter | `src/unitracker/lift/<name>.py` | native module data to the internal representation (*The internal representation*) |
| backend entry | `src/unitracker/lift/backends.py` | detection, and dispatch to the lifter and the native player |
| reference tune | `tests/conftest.py` | an HVSC tune fetched into `.tunecache/` and checked against the native replay |

The native replay comes from a Python package built on `pysidtracker` (`pygoattracker`,
`pydefmon`, `pysidwizard`) that decodes the module and runs the replay as a
`pysidtracker.MemPlayer`; its `render_grid(n)` is the reference register grid. A port needs one,
as a dependency in `pyproject.toml`.

Start from the existing port closest to the target player:

| port | player | read it for |
|---|---|---|
| `goattracker2` | instruments pointing into tables whose rows are waveforms, notes and commands; pattern commands; its own tempo counter | command dispatch through playroutine `records` (`tick0`, `tickn`, `wavecmd`), one codec shape per command row, the gate-timer lookahead |
| `sidwizard` | an instrument header plus WF/arp, pulse and filter programs; a pattern row with an instrument column and an FX column | header bytes as instrument keys, two event slots, envelope nibble commands, `lifetime` flags |
| `defmon` | no native instrument: sidTAB programs armed per step on two layers; one step per tick | `instrument: writes_as`, event restarts (`restart: {command}`), `fixed` init records, a `global` unit |

**Files:** none yet. **Verify** the toolchain and the existing ports:
`PYTHONPATH=src .venv/bin/python tools/reference_check.py out/porting` prints
`round trip ok; first mismatch: None; forbidden: none` for each reference tune.

## 2. Transcribing the replay

The generic player has no player-specific code: every cell, branch and table the replay's output
depends on is in the YAML. The schema is *The playroutine*; the action language *Actions*,
*Cells and expressions* and *The bounded accumulator*. Transcribe the replay in its own order:

| replay | playroutine |
|---|---|
| build constants, default tempo | `constants`, which the lifter sets per tune; `defaults` (clock, voices) |
| frequency and other lookup tables | `tables` (a song patches them, see *Document*); `note_offset` |
| zero-page and self-modified operands, per voice or shared | `voice` and `global` cells, each with the `width` of its 6502 location; a scratch value has no width |
| jump tables (command dispatch) | playroutine-owned `records`, run with `run: {record, index, field}` |
| module tables (instruments, wave, pulse, filter tables) | `record_types` and their templates (section 3) |
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
`ev_cmd`/`ev_arg` and `ev_cmd2`/`ev_arg2`, `at_phrase_start` and `ended` (*The cursor*). Map
each pattern column onto them; the lifter writes events in the same form (section 6). An event
carries at most two commands, its two slots (*Events*).

**The ghost and the canonical flush.** The ghost gives each register after the tick, and the
player flushes the changed registers in one canonical order (*Ticks, the ghost and the canonical
flush*). The replay's write order does not matter: the grid is the state after each tick. A
replay that writes registers from values latched on the previous call transcribes the latch as
cells (`defmon`'s `latch` proc and `o_*` cells).

**The `onset` cell and the zeroed SID.** The player starts from a zeroed SID and carries none of
the native init writes (*Checking against the native player*). Declare a 1-bit voice cell, set
it on the tick the voice's registers first hold its first note, and name it in `onset:`. The
check compares each voice from its onset and `$D415`-`$D418` from the earliest onset. Set it
where the replay writes the note's frequency, which need not be where the event is read
(`goattracker2` sets it in `wnote`, when a wavetable row first sets the frequency).

**Verify before converting.** With a first lifter (section 6) returning `expressions=False`
(native modulations, no conversion), compare the transcription with the native replay:

```sh
PYTHONPATH=src .venv/bin/python -c '
import sys, numpy as np
from pathlib import Path
from unitracker.lift import backends
from unitracker.player.vm import SongPlayer
raw = Path(sys.argv[1]).read_bytes()
theirs = np.asarray(backends.native_player(raw).render_grid(3007), dtype=np.uint8)
print(SongPlayer(backends.lift(raw, expressions=False)).mismatch(theirs))
' TUNE.sid
```

It prints `None`, or the first `(tick, register)` that differs. Once the song writes as a
document, `tools/grid_diff.py` prints both grids over a tick range, marking differing registers:

```sh
PYTHONPATH=src .venv/bin/python tools/grid_diff.py song.yaml TUNE.sid --from 94 --to 98 --ticks 100
```

**Files:** `src/unitracker/playroutines/<name>.yaml`. **Verify:** the snippet above prints `None`.

## 3. Record types and codecs

Declare each native table as a record type (*Record types*):

| native table | keys |
|---|---|
| instruments | `form: map`, `fields` with their defaults, `refs` to the programs they start, `none`, and `pin` for an index the replay reads directly (GoatTracker's instrument 1) |
| a table of programs with a next pointer or jump | `chain` (the next-row field), `none`, `stop` (a row after which the program ends, `defmon`'s frozen row), `end` (raw fields set on a program's last row) |
| rows whose effect depends on their kind | templates (any other key, such as `do` and `each`), reading `row.<field>` |
| module state carried through init | `fixed: true` (`defmon`'s `vinit`, `ginit`) |
| a modulated program | `expr: x`, the field holding a row's expression |
| raw rows the format no longer has | `retired: {expression: reason}` |

`writes_as` names the record type the table is written as (`ins`, `ctrl`, `pw`, `filter`,
`restart`; the table in *Definitions*), and `prefix` its name prefix when that is not the written
name's first letter. A field for which 0 is meaningful defaults to -1, "absent" (the `restart`
rows, `defmon`'s sidTAB columns).

**Codec shapes.** Each `codec` entry is one row kind: `when` over the raw fields, the constants
and, for a program row, its place (`first`, `last`); `keys` from raw fields to shared keys; `raw`
back; `keep` for raw fields written under their own names (*Codecs*). Write every row kind onto
the shared keys (*Shared vocabulary*), each in its one meaning in chip units. The reader accepts a
written row only when encoding its raw inverse gives the row again, so a shape whose `keys` and
`raw` disagree refuses its own rows. Command codes go in `commands`: code to `{name, ref, when,
arg, raw, inline}`, or `{name, nibble, slot}` for an envelope command.

**A document names no player.** No key, command or record type is one player's own. What the
player cannot play has no shape, so the reader refuses it naming the key or command and the
player (`sidwizard: the player can't express !tempo:2`), and the writer refuses a raw value that
would not read back. Never add a key for one player: resolve the native row at lift time into a
form that has shared keys (section 6), or fail the lift naming it. A shared form two players need
and the format lacks is a format change (`songio.VERSION`, the `RETIRED` and `RETIRED_ROW_KEYS`
patterns in `codec.py` and `retired.FIELDS`, format.md), not part of the port.

**The vocabulary and command-table tests.** `tests/test_command_table.py` generates format.md's
command table and `ctrl` row key table from every YAML in the library, so a new playroutine adds a
column; the failing assertion prints the expected table to paste into format.md.
`tests/test_codec.py`'s `vocabulary(doc)` checks that a lifted document uses only the fixed
vocabulary, each shared key in its one meaning: add the player to `LIBS`, `INS_KEYS`,
`SHARED_OUTSIDE_ROWS` (and `ROW_KEEP`, `INIT_KEYS` where they apply), and the reference tune
with its expected command set to `test_every_song_uses_only_the_shared_vocabulary`. Add the
note-index check to `test_note_offsets_follow_the_frequency_tables`.

`unitracker-modfit keywords <name>` lists each command and record field with the shared key it
is written via and its signature against the other playroutines' (modfit.md, *Signatures*): a
field with another player's effect under a different key is a vocabulary error.

**Files:** the playroutine's `record_types` and `commands`; `tests/test_codec.py`;
`docs/format.md` (the two generated tables, the *Definitions* table). **Verify:**
`PYTHONPATH=src .venv/bin/python -m pytest -n auto tests/test_codec.py tests/test_command_table.py`
and `PYTHONPATH=src .venv/bin/unitracker-modfit --out out/porting/keywords.txt keywords <name>`.

## 4. Modulators

A unit is a cell the song drives by an integer expression in `v`, `φ` and `N`, evaluated at each
of the unit's firings (*Modulators*). Declare one per native modulation the lifter converts: a
pitch effect (`key: freq`), a pulse sweep (`pw`) or a filter sweep (`cut`). The unit keys are in
*Modulator declarations*:

| key | what it transcribes |
|---|---|
| `cell`, `scope` | the cell the replay modulates (`hi:lo[:k]` for a pair); `global` for one cell the voices share |
| `fire`, `pre`, `post` | one firing: `v` is read before `pre` runs and the value stored after `post` |
| `hooks`, `program` | the starts: a function writing a `written` cell, changing the `key` cells, or writing a `pointer` to a program that sets one of `program.fields` |
| `x` | the expression a start selects; 0 leaves the playroutine's own firing |
| `label` | the cells grouping occurrences, usually the program pointer |
| `site`, `retire`, `command`, `commands` | where the lifter writes the expression, the native fields it replaces, and the command codes `!freq` replaces |
| `chip` | the chip map, below |

A start the `site` does not cover is written as the event's `!freq`. When `x` reads the site's
own record field (GoatTracker's command unit falls back to the instrument's `freq` on a step
without a command), each step the start spans without a command gets the `!freq`; otherwise
(SID-Wizard's pitch unit, whose cell a commandless row leaves alone) only the step that starts
it does (`convert.falls_back`).

The top level of `modulators` gives what expressions read: `freq` (the word `F(k)` reads),
`notes`, `transpose`, `legato`, and the named `tables` and `functions`.

**The chip map.** `chip: {width, value, store}` gives the unit's value `v` from its cells
(`value`, `width` bits) and writes a value back (`store`, run only when the cells do not already
give it). `v` must be a bijective image of the cell: each cell state the firings reach gives its
own `v`, and `store` inverts `value`. A map that loses information (a clamp, a floor) makes two
cell states one `v`, and no recurrence over `v` is exact. Keep `value` a linear image of the cell,
plus at most a term that does not depend on the cell, wrapped to `width`, and move the rest into
the ghost, where the register is written: GoatTracker's cutoff is `filtercutoff << 3`,
SID-Wizard's pulse is `pwhi:pwlo` plus its keyboard-tracking term in the note (`store` subtracts
it), and `defmon`'s `acid B` is its whole accumulator, with the floor, the `dbl` shift and the
offset in the ghost's `$D416`. A value the replay carries from earlier
playback becomes a constant of the start's expression, which the lifter resolves (*Evaluation*).

**Running `unitracker-modfit`.** The `exprs` report lifts the tune with native modulations,
converts it, and gives per unit and label the occurrences, distinct starts, each start's phase,
value update, closed form, proof and the invariant it is proved under, the chip map, and where
each text is written (modfit.md, *Phase and value expressions*, *Conversion*). A start that does
not prove fails the lift, naming the unit, its program and the start (*Lifting*).
`tools/occurrences.py` lists the occurrences one per line with the start cells asked for, and
`unitracker-modfit command <name>` composes each command's setup and per-tick effect, which
checks a dispatch transcription before any tune uses it:

```sh
PYTHONPATH=src .venv/bin/unitracker-modfit --out out/porting/exprs.txt exprs TUNE.sid
PYTHONPATH=src .venv/bin/python tools/occurrences.py TUNE.sid --unit "pulse A" --cells rowA
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
| hard restart | a `restart` program anchored to the note: lookahead rows with `hold`, `gate`, `ctrl`, `ad`, `sr`, then the note's first-tick row (*Hard restart*) | a `restart` record type with shapes on `first` and `last`; an instrument field `restart`, or `restart: {command: c}` for a player whose notes arm it on the steps before them | the lookahead and first-tick table in *Hard restart* |
| chords and arpeggios | a `ctrl` program of relative-note rows, a different chord started with `!run` (*Chords*) | `note: +n` in the wave or arp table's shapes; the lifter inlines a separate chord table into the program | the *SID-Wizard* backend paragraph |
| filter | `res`, `route` (a mask, or the edit `+ m`, `-`), `mode`, `cut`, and `!vol` (*Filter*) | filter program shapes; `rel` on `route` for an edit | the player table in *Filter* |
| lifetime | `!pulse:0`, `!filter:0` on the note (*Lifetime*) | `lifetime: {pulse: {field, flag}, filter: {field, flag}}` | the player table in *Lifetime* |
| relative control | `ctrl: ^n`, the exclusive-or mask the voice's control output keeps (*Shared vocabulary*) | `eor` on the `ctrl` key | `defmon` only; the others refuse it |
| legato | `!legato:0` (*Legato*) | `modulators.legato: {cell, command}` | the command table |
| tempo | the tempo commands and constants (*Shared vocabulary*) | `commands` entries with `when`, `arg`, `raw` | the command table; a tempo command no entry declares is refused by name |

The writer and reader resolve the context these forms need by walking each voice's plays:
`src/unitracker/format/events/`: `lifetime.py` (the instrument a note plays), `envelope.py` (an
envelope nibble command as the byte it writes), and the event folds `fold.py`, `instrument.py` (the
record an event's columns arm) and `restart.py` (a restart run on the steps before its note). They read the playroutine's
`lifetime`, `commands` with `nibble`, `instrument: writes_as` and `restart` declarations, so a port
declares them rather than codes them; each raises its own error naming what has no written form.

**Files:** the playroutine's `restart` record type, `lifetime`, `restart`, `instrument`, codec
shapes and `commands`. **Verify:**
`PYTHONPATH=src .venv/bin/python -m pytest -n auto tests/test_lifetime.py tests/test_envelope.py tests/test_eventrestart.py`.

## 6. The lifter

`src/unitracker/lift/<name>.py` defines `LiftError(ValueError)` and
`lift(raw: bytes, subtune, expressions: bool = True) -> dict`, which decodes the module with the
native package and returns the internal representation:

| key | content |
|---|---|
| `unitracker` | `"ir"` |
| `meta` | `songio.song_meta(header, "<name> subtune N")` |
| `hints` | `ticks_per_step` where the lifter derives one |
| `clock`, `voices` | from the PSID header and the build |
| `tables` | the module's tables the playroutine reads (the frequency table) |
| `records` | `{record type: {index: {field: value}}}`, under the playroutine's own type names |
| `tracks` | per voice `{"plays": [{"phrase": name, "transpose": t, "repeat": n}], "loop": k}` |
| `phrases` | `{name: [event]}`; an event has `note` (the player's note index, `off` or `on`), `instrument`, `command: [code, arg]` or `commands: [[c, a], [c, a]]`, and `steps` (`vm.fold_empty`) |
| `playroutine` | `songio.library("<name>")` with `constants` set from the build |

It ends `return convert(ir) if expressions else ir` (`unitracker.modfit.convert.convert`).
Register it in `backends.py`: `detect` recognises the image (before the GoatTracker fallback,
which recognises a tune by decompiling it), and `lift` and `native_player` dispatch to the
lifter and the native player. The CLI, the tools and the tests all go through these three.

**Resolved at lift time.** A document carries only what the composer's rows and definitions
imply, so the lifter resolves what the native data leaves implicit:

| what | example |
|---|---|
| the build | constants (`adparam`, `deftempo`, `defmon`'s `floor` and `dbl`); `check_build` refuses builds and features the playroutine does not cover |
| carried modulator values | a slide's start frequency, a delay counter, an ACID direction: `convert` writes each as a constant of its start's expression |
| instrument context | the voice's instrument at each note: SID-Wizard's octave shift into the notes, its envelope bytes, a first note that selects no instrument |
| module state through init | `defmon`'s `vinit` and `ginit`; a cell the replay's init sets is refused, naming it |
| native rows with no shared form | rewritten to rows that have one when a replay proves it unheard (GoatTracker's `unpause` of `$F7` and `$FD`) |

**What fails loudly.** Raise `LiftError` naming the row and what is not covered
(`ctrltab row 12: wavetable command $F0 stops the playroutine: not lifted`, `voice 1's
first note selects no instrument`). Never fall back to an
approximation: acceptance is the exact grid, and an unproved modulation raises
`ConversionError`. List what the lifter refuses in the port's paragraph of *Backends*.

**Files:** `src/unitracker/lift/<name>.py`, `src/unitracker/lift/backends.py`, `pyproject.toml` (the native
package, the description), `docs/format.md` (the *Backends* table and paragraph, the timing and
command subsets). **Verify:**

```sh
PYTHONPATH=src .venv/bin/unitracker-lift TUNE.sid -o out/porting/song.yaml
PYTHONPATH=src .venv/bin/unitracker-play out/porting/song.yaml --check TUNE.sid
```

`--check` prints `match: 3007 ticks identical to the native player`.

## 7. Tests and acceptance

| test | file | shape |
|---|---|---|
| reference tune | `tests/conftest.py` | a session fixture: `resolve_tune("MUSICIANS/.../Tune.sid", cache_dir=CACHE)` (local `$HVSC`, `.tunecache/`, or the mirror), `pytest.skip` when unavailable; the path also goes in `tools/reference_check.py`'s `TUNES` |
| synthetic tunes | `tests/conftest.py` | a builder like `gt_tune`, returning `(decoded tune, native player)` for a one-feature tune, where the native package can build modules |
| conformance | `tests/test_conformance.py` | for `embed` False and True: lift, `text = songio.dumps(ir)`, `song = songio.loads(text)`, `songio.dumps(song) == text` (the canonical round trip); the native grid over the intro and two loops, non-trivial (`len(np.unique(theirs, axis=0))`); `SongPlayer(song).mismatch(theirs) is None`; and the tune in `test_lift_is_deterministic` |
| dispatch | `tests/test_backends.py` | `detect`, `lift` and `native_player` on the tune |
| vocabulary, command tables | `tests/test_codec.py`, `tests/test_command_table.py` | section 3 |
| lifter | `tests/test_<name>.py` | each `LiftError`, with `pytest.raises(LiftError, match=...)` |
| exprs report | `unitracker-modfit exprs` | section 4 |
| optimiser | `tests/conftest.py` `OPTIMISED` | the tune with a subset of passes that keeps the suite's time bounded |

**The optimiser.** `unitracker-optimise` rewrites a song into a smaller document proved to play
the same grid (optimise.md). Run it on the lifted reference tune, check the output against the
native player, and read its `--report` (orthogonality.md): a kept edit on a freshly lifted tune is
a redundancy the lifter or codec writes (a key the context already gives, a command every note of
an instrument carries), which the lifter should not write.

```sh
PYTHONPATH=src .venv/bin/unitracker-optimise out/porting/song.yaml -o out/porting/song.opt.yaml --report out/porting/orthogonality.md
PYTHONPATH=src .venv/bin/python tools/grid_diff.py out/porting/song.opt.yaml TUNE.sid --ticks 3007 --to 0
```

**Acceptance:** `tools/grid_diff.py song.yaml TUNE.sid --ticks 3007 --to 0` prints
`first mismatch: None` for the lifted and the optimised song; `tools/reference_check.py`, with
the native table and column names as `--forbid`, prints `round trip ok` and `forbidden: none`;
`tools/token_report.py song.yaml` counts the document's tokens; coverage stays above 85% and the
lint is clean.

**Files:** `tests/conftest.py`, `tests/test_conformance.py`, `tests/test_backends.py`,
`tests/test_<name>.py`, `tools/reference_check.py`. **Verify:**

```sh
PYTHONPATH=src .venv/bin/black --check src tests
PYTHONPATH=src .venv/bin/pylint src
PYTHONPATH=src .venv/bin/pylint --disable=missing-function-docstring tests
PYTHONPATH=src .venv/bin/python -m pytest -n auto --cov=unitracker --cov-fail-under=85
```

## 8. Checklist

| # | step | files | command (each with `PYTHONPATH=src`) |
|---|---|---|---|
| 1 | pick the closest port; fetch the reference tunes | `tests/conftest.py` | `.venv/bin/python tools/reference_check.py out/porting` |
| 2 | transcribe the replay: cells, tables, procs, `init`, `tick`, ghost, `onset` | `playroutines/<name>.yaml` | the `expressions=False` snippet of section 2 prints `None` |
| 3 | a first lifter and its registration | `lift/<name>.py`, `lift/backends.py`, `pyproject.toml` | the same snippet, through `backends.lift` and `backends.native_player` |
| 4 | record types, codec shapes and commands on the shared keys | `playroutines/<name>.yaml` | `.venv/bin/unitracker-modfit keywords <name>` |
| 5 | hard restart, chords, filter, lifetime, relative control in their shared forms | `playroutines/<name>.yaml` | `.venv/bin/python -m pytest -n auto tests/test_lifetime.py tests/test_envelope.py tests/test_eventrestart.py` |
| 6 | modulator units and chip maps | `playroutines/<name>.yaml` | `.venv/bin/unitracker-modfit --out out/porting/exprs.txt exprs TUNE.sid` |
| 7 | lift with expressions; check against the native player | `<name>.py` | `.venv/bin/unitracker-lift TUNE.sid -o out/porting/song.yaml`, then `.venv/bin/unitracker-play out/porting/song.yaml --check TUNE.sid` |
| 8 | the register grid from the voices' onsets | | `.venv/bin/python tools/grid_diff.py out/porting/song.yaml TUNE.sid --ticks 3007 --to 0` |
| 9 | vocabulary and command tables; format.md | `tests/test_codec.py`, `docs/format.md` | `.venv/bin/python -m pytest -n auto tests/test_codec.py tests/test_command_table.py` |
| 10 | conformance, dispatch and lifter tests | `tests/` | `.venv/bin/python -m pytest -n auto tests/test_conformance.py tests/test_backends.py tests/test_<name>.py` |
| 11 | the optimiser on the lifted tune | `tests/conftest.py` | `.venv/bin/unitracker-optimise out/porting/song.yaml -o out/porting/song.opt.yaml`, then step 8 on it |
| 12 | canonical round trip and forbidden names | `tools/reference_check.py` | `.venv/bin/python tools/reference_check.py out/porting --forbid <native names>` |
| 13 | lint and the full suite | | the four commands of section 7 |
