# Lifting

A lifter decodes a native tune and writes each native modulation as an expression;
`unitracker.lift.backends.lift` dispatches to the lifter of the backend that recognises the tune,
and `expressions=False` returns the internal representation with the native modulations.

A lifter is a subclass of `unitracker.lift.base.Lift`: GoatTracker 2's is
`goattracker.GoatTrackerLift`, SID-Wizard's `sidwizard.SidWizardLift`; defMON (`defmon.lift`) is to
follow. `Lift.from_sid(raw, subtune, expressions)` decodes the tune, and `run()` takes it through
the passes `passes()` names in order, after `check_build` refuses a build the playroutine does not
cover:

| pass | content |
|---|---|
| `structure` | the native module transcribed to [the internal representation](#the-internal-representation) |
| each of `normalisations` | a player-specific rewrite `(lift, song) -> song` (GoatTracker 2, each proved by a replay: `unpause`, `explicit_swing`, `unmark`; SID-Wizard: `first_note_selects`, `chord_runs`, `fold_octaves`) |
| `convert` | with `expressions` only: the modulations as expressions (GoatTracker 2 then writes a toneporta of speed 0 as `!freq`, `snapped`) |

`stages()` yields each pass's name and the song after it. The conversion
(`unitracker.modfit.convert`, see [modfit.md](../modulation/modfit.md)) must leave the song playing
the native register grid exactly, each voice from its onset (see [Checking against the native
player](schema.md#checking-against-the-native-player)); a start whose expression is not proved, and
a native row with no written form, fail the lift, naming them. Each playroutine's file lists what
its lifter does not cover.

| playroutine | tune | step |
|---|---|---|
| [`goattracker2`](goattracker2.md) | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row |
| [`sidwizard`](sidwizard.md) | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row |
| [`defmon`](defmon.md) | defMON replay `.sid` of a build of the `$1000` replay's code (pydefmon) | one tick |
## The internal representation

`unitracker.format.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records with a `next` field per program row,
per-voice tracks of plays, phrases of event dicts, the expressions as
`exprs: {index: text}`, and the full playroutine and tables. Lifters produce this
representation and `songio.dumps` writes it.
