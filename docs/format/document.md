# Document

A unitracker document is the language of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar)
([`canonical.lark`](https://github.com/anarkiwi/unitracker-grammar/blob/main/src/unitracker_grammar/canonical.lark))
with the meaning of [unitracker-player](https://github.com/anarkiwi/unitracker-player)'s
[semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md): it names
no player. `unitracker.document.write(ir)` writes it from the lifter's song, and `unitracker-lift`
proves it: played by unitracker-player, it must give the native player's register grid (see
[Lifting](../playroutines/lifting.md)). `document.native_state(ir)` is the one native replay the
writer reads (the note table, the notes the song reads, the tables, the voices' onsets), to its
state's recurrence and a period more, or to its end where every voice's track ends;
`write(ir, native)` takes one already made.

From the lifter's song the writer emits:

| part | written |
|---|---|
| header | no `playroutine`; `clock`, `voices` and `tempo` only where they differ from PAL (985248 Hz, 19656 cycles per tick), 3 and 6; a playroutine without a tempo steps once per tick (`tempo: 1`) |
| `tables` | each table an expression indexes (`T[k]`) as a list; `G`, the native note table by format note, where it differs from `F` at a note the song reads, holding the whole table over the notes `F` spans and those the song reads (its trailing zeros left out); then every `F(k)` reads `G[k]` and an instrument without `freq` has `freq: G[N]` |
| frequency | the lifter's song reads a frequency expression's `v` as the offset from the note's frequency; the document reads it as the frequency word: `G[N] + e` with `v` read as `(v - G[N]) % 65536`, and 0 until the first native firing of an instrument's, a note-started program's or a note's command's frequency |
| firing ticks | `φ` counts ticks: each expression is re-timed from its native unit's firings to its modulators' ticks (`unitracker.document.retime`), `φ` read as the firing count `φ - Σ (φ >= s)` over the ticks `s` the unit holds on and a recurrence's update times a hold factor that is 0 on them, every `T`th tick from `o < T` spelled `(φ + T - o) // T` and `(φ % T == o)`; an event whose ticks differ from those of its expression's other modulators plays its own through its command, a copy of the program it runs or a copy of its instrument |
| `init` | one event per voice: the global registers the native init sets (`!resfilt`, `!mode`, `!vol`, `!cut`), `vinit` and `ginit` (GoatTracker 2: the instrument the native init gives a voice that plays a note or runs a program before it names one, `.iN`; defMON: a legato note for the base note, `!pw`, `!freq`, `!cut`), and the lead-in's tempo |
| rows | a row where a voice's phrase ends, counted in ticks as unitracker-player times its steps (each voice's tempo pair and swing, `!tempo` and `!vtempo`), until every voice's state repeats: the loop row |
| lead-in | a first row of rests, timed by the init row, delaying the song to the tick the native player sounds its first note |
| timing | GoatTracker 2 and SID-Wizard: a restart's first-tick row, its gate and the instrument's envelope a tick before the note, holding the frequency and stopping the control program; GoatTracker 2: a note keeps the gate its restart leaves; defMON: a row that sets the gate clears the mask; SID-Wizard: the filter is routed to each voice whose instrument has a filter program |
| names | a program an instrument field names under another type's prefix is defined again under the field's |

A row the lifter's song holds in its type's native `form`, a record type outside the grammar's, and an
instrument field outside its keys are lifting gaps: the writer refuses them, naming the record
and the row (`GenericError`). Every document it writes parses with `unitracker_grammar.parse`.

## Example

The start of the generic document of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.20
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
tempo: 12
tables:
  G: [26368, 56576, 30976, 15360, 10496, 17408, 36096, 2048, 47104, 41216, 50432, 10240, 52480, 47616,
    ...
init: [-!vol:15!tempo:2, -, -]
song:
  - def:
      phrase:
        p17: [-, -!tempo:12]
    play: [p17, p17, p17]
  - def:
      ins:
        i1: {ad: 1, sr: 136, ctrl: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: G#5},
            {ctrl: 65, note: +0}, end], pw: 16*abs((φ - (φ >= 11) - (φ >= 15) - 32) % 128 - 64) + 512,
          filter: [{cut: 1024, res: 12, route: 1, mode: 16}, end], freq: 'G[N]', restart: [{hold: 4, gate: 0,
              ad: 15, sr: 0}, {ctrl: 9, gate: 1, ad: 1, sr: 136, freq: '(v - G[N]) % 65536 + G[N]', run: c4},
            {}, end]}
        ...
      ctrl:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0}, {wait: 1,
            note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
        ...
      phrase:
        p1: [C2.i1!pw:x5*3, C4.i2, off, C3.i1, C2!pw:x5*3, C4.i2!pw:x6*3, ...]
        p2: [off*3, G4.i3!run:c1*3, off*3, G4!run:c1*3, ...]
        p3: [off!tempo:16*48]
      x:
        x5: 16*abs((φ - (φ + 5) // 16 - (φ >= 47) - 32) % 128 - 64) + 512
        ...
        x9: ((v - G[N]) % 65536)*(φ >= 3) - 32*(φ == 15) + 32*(φ >= 3) + G[N]
        ...
    play: [p1, p2, p3]
  - def:
      ...
      phrase:
        ...
        p6: [off*3, A#3.i4!freq:x9, C4!freq:x2!legato, D4, D#4*2, C4, G4*2, C5, G#4!freq:x9, ...]
    play: [p4, p5, p6]
  ...
  - def:
      phrase:
        p16: [off!tempo:16, -!freq:none*47]
    play: [p1, p2, p16]
  - {loop: 2}
```
