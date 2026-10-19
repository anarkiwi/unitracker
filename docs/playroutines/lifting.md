# Lifting

A lifter decodes a native tune and writes each native modulation as an expression;
`unitracker.lift.backends.lift` dispatches to the lifter of the backend that recognises the tune,
and `expressions=False` returns the lifter's song with the native modulations.

A lifter is a subclass of `unitracker.lift.base.Lift`: GoatTracker 2's is
`goattracker.GoatTrackerLift`, SID-Wizard's `sidwizard.SidWizardLift` and defMON's
`defmon.DefmonLift`. `Lift.from_sid(raw, subtune, expressions)` decodes the tune, and `run()` takes it through
the passes `passes()` names in order, after `check_build` refuses a build the playroutine does not
cover:

| pass | content |
|---|---|
| `structure` | the native module transcribed to the lifter's song (an in-memory dict) |
| each of `normalisations` | a player-specific rewrite `(lift, song) -> song` (GoatTracker 2, each proved by a replay: `unpause`, `explicit_swing`, `unmark`; SID-Wizard: `first_note_selects`, `chord_runs`, `fold_octaves`; defMON: `unheard_init`, proved by the replay's grid) |
| `convert` | with `expressions` only: the modulations as expressions (GoatTracker 2 then writes a toneporta of speed 0 as `!freq`, `snapped`) |
| `canonical` | with `expressions` only: modulation written shattered in the format's canonical form, by the optimiser's canonical passes ([Canonical passes](../optimise.md#canonical-passes)), each proved on its replay and its generic document; cached by content beside the conversion's cache; a song whose state does not recur, or that has no generic document, is left as converted |
| `verify` | with `expressions` only, leaving the song as it is: its [generic document](../format/document.md), played by unitracker-player for the conversion's ticks (the song's recurrence and a period more) against the native player's grid; `verdict` holds the first mismatch each voice from its first note and from tick 0, `document` the text, and `gap` the lifting gap that keeps it from being written or played |

`stages()` yields each pass's name and the song after it. `unitracker-lift` writes the generic
document and exits nonzero with the verdict on its last line when the document does not play the
native grid; `backends.verify(document_text, raw_sid, subtune)` proves a document a tool holds. The conversion
(`unitracker.modfit.convert`, see [modfit.md](../modulation/modfit.md)) must leave the lifter's
song replaying the native register grid exactly on the lifter's VM, each voice from its onset
(see [Checking against the native player](schema.md#checking-against-the-native-player)); a start whose expression is not proved, and
a native row with no written form, fail the lift, naming them. Each playroutine's file lists what
its lifter does not cover.

| playroutine | tune | step |
|---|---|---|
| [`goattracker2`](goattracker2.md) | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row |
| [`sidwizard`](sidwizard.md) | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row |
| [`defmon`](defmon.md) | defMON replay `.sid` of a build of the `$1000` replay's code (pydefmon) | one tick |
