# The unitracker document

The unitracker document is the language of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar) with the meaning of
[unitracker-player](https://github.com/anarkiwi/unitracker-player)'s
[semantics](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md): it names
no player. `unitracker-lift` writes it from a native tune and proves it: played by
unitracker-player, it must give the native player's register grid.

| doc | content |
|---|---|
| [format/document.md](format/document.md) | the generic document and what the writer emits |
| [format/vocabulary.md](format/vocabulary.md) | the shared row keys and commands the lifters decode native rows into; chords, filter, hard restart, lifetime, envelope commands, instrument columns |
| [modulation/expressions.md](modulation/expressions.md) | the expressions the lifters decode native modulations into: grammar, evaluation, units, where expressions go, legato |
| [modulation/modfit.md](modulation/modfit.md) | the modulation analysis and the proved conversion to expressions (`unitracker-modfit`) |
| [playroutines/schema.md](playroutines/schema.md) | the lifters' decoding declarations: record types, codecs, actions, modulator declarations, the cursor, the flush |
| [playroutines/lifting.md](playroutines/lifting.md) | the lifters and the lifter's song |
| [playroutines/goattracker2.md](playroutines/goattracker2.md), [sidwizard.md](playroutines/sidwizard.md), [defmon.md](playroutines/defmon.md) | what each lifter decodes, covers and refuses |

## Example

The start of the generic document of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.20
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
tempo: 12
tables:
  G: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 744, 0, 835, ...]
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
            {ctrl: 65, note: +0}, end], pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: [{cut: 1024, res: 12,
              route: 1, mode: 16}, end], freq: 'G[N]', restart: [{hold: 4, gate: 0, ad: 15, sr: 0}, {
              ctrl: 9, gate: 1, ad: 1, sr: 136, freq: '(v - G[N]) % 65536 + G[N]', run: c4}, {}, end]}
        ...
      ctrl:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0}, {wait: 1,
            note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
        ...
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!run:c1*3, off*3, G4!run:c1*3, ...]
        p3: [off!tempo:16*48]
    play: [p1, p2, p3]
  - def:
      ...
      x:
        x1: (v - G[N]) % 65536 + G[N] + 32
        ...
        x3: 64*((φ - 3) % 12 >= 6) + (v - G[N]) % 65536 + G[N] - 32
      phrase:
        ...
        p6: [off*3, A#3.i4!freq:x1, C4!freq:x2!legato, D4, D#4*2, C4, G4*2, C5, G#4!freq:x1, ...]
    play: [p4, p5, p6]
  ...
  - def:
      phrase:
        p16: [off!tempo:16, -!freq:none*47]
    play: [p1, p2, p16]
  - {loop: 2}
```
