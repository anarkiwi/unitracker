# Lifting

A lifter (`unitracker.lift.backends.lift`, dispatching to `goattracker.lift`, `defmon.lift` and
`sidwizard.lift`) decodes a native tune and writes each native modulation as an expression;
`expressions=False` returns the internal representation with the native modulations. The conversion
(`unitracker.modfit.convert`, see [modfit.md](../modulation/modfit.md)) must leave the song playing
the native register grid exactly, each voice from its onset (see [Checking against the native
player](schema.md#checking-against-the-native-player)); a start whose expression is not proved, and
a native row with no written form, fail the lift, naming them. Each playroutine's file lists what
its lifter does not cover.

| playroutine | tune | step |
|---|---|---|
| [`goattracker2`](goattracker2.md) | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row |
| [`sidwizard`](sidwizard.md) | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row |
| [`defmon`](defmon.md) | defMON `$1000` replay `.sid` (pydefmon) | one tick |
## The internal representation

`unitracker.format.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records with a `next` field per program row,
per-voice tracks of plays, phrases of event dicts, the expressions as
`exprs: {index: text}`, and the full playroutine and tables. Lifters produce this
representation and `songio.dumps` writes it.
