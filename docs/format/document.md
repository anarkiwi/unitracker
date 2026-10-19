# Document

A unitracker document is the language of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar)
([`canonical.lark`](https://github.com/anarkiwi/unitracker-grammar/blob/main/src/unitracker_grammar/canonical.lark)):
it names no player, and
[unitracker-player](https://github.com/anarkiwi/unitracker-player) plays it as its
[semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md) say.
`unitracker-lift` writes it (`unitracker.format.generic.dumps`) and proves it: the document,
played by unitracker-player, must give the native player's register grid (see
[Lifting](../playroutines/lifting.md)).

From the lifter's song the writer emits:

| part | written |
|---|---|
| header | no `playroutine`; `clock`, `voices` and `tempo` only where they differ from PAL (985248 Hz, 19656 cycles per tick), 3 and 6; a playroutine without a tempo steps once per tick (`tempo: 1`) |
| `tables` | each table an expression indexes (`T[k]`) as a list; `G`, the native note table by format note, where it differs from `F` at a note the song reads, holding only the entries the song reads (0 elsewhere); then every `F(k)` reads `G[k]` and an instrument without `freq` has `freq: G[N]` |
| frequency | the lifter's song reads a frequency expression's `v` as the offset from the note's frequency; the document reads it as the frequency word: `G[N] + e` with `v` read as `(v - G[N]) % 65536`, and 0 on the first tick of an instrument's or a note-started program's frequency |
| `init` | one event per voice: the global registers the native init sets (`!resfilt`, `!mode`, `!vol`, `!cut`), defMON's `vinit` and `ginit` (a legato note for the base note, `!pw`, `!freq`, `!cut`), and the lead-in's tempo |
| lead-in | a first row of rests, timed by the init row, delaying the song to the tick the native player sounds its first note |
| timing | GoatTracker 2 and SID-Wizard: a restart's first-tick row, its gate and the instrument's envelope a tick before the note, holding the frequency and stopping the control program; GoatTracker 2: a note keeps the gate its restart leaves; defMON: a row that sets the gate clears the mask; SID-Wizard: the filter is routed to each voice whose instrument has a filter program |
| names | a program an instrument field names under another type's prefix is defined again under the field's |

A row the lifter's song holds in its type's native `form`, a record type outside the grammar's, and an
instrument field outside its keys are lifting gaps: the writer refuses them, naming the record
and the row (`GenericError`). Every document it writes parses with `unitracker_grammar.parse`.
