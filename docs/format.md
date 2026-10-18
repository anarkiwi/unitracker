# unitracker song format (v1.20)

A unitracker song is one YAML document, played by the generic player (`unitracker-play`) on an
emulated SID. Any prefix of the document is a playable partial song: a short header, then one
timeline in which every instrument, program, expression and phrase is defined in the row where it
is first used. Everything a player does is in its **playroutine**, which the song references
from the library in `src/unitracker/playroutines/`.

| doc | content |
|---|---|
| [format/document.md](format/document.md) | top-level keys, embedding, the timeline, definitions, events, the canonical form |
| [format/vocabulary.md](format/vocabulary.md) | the shared row keys and commands; chords, filter, hard restart, lifetime, envelope commands, instrument columns |
| [modulation/expressions.md](modulation/expressions.md) | modulator expressions: grammar, evaluation, units, where expressions go, legato |
| [modulation/modfit.md](modulation/modfit.md) | the modulation analysis and the proved conversion to expressions (`unitracker-modfit`) |
| [playroutines/schema.md](playroutines/schema.md) | the playroutine definition: record types, codecs, actions, modulator declarations, the cursor, the flush |
| [playroutines/lifting.md](playroutines/lifting.md) | the internal representation and the lifters |
| [playroutines/goattracker2.md](playroutines/goattracker2.md), [sidwizard.md](playroutines/sidwizard.md), [defmon.md](playroutines/defmon.md) | what each library playroutine and its lifter support and refuse |

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.20
playroutine: {ref: goattracker2}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
tempo: 12
song:
  - def:
      restart:
        r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]
      ins:
        i1: {ad: 1, sr: 136, ctrl: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: G#5},
            {ctrl: 65, note: +0}, end], pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: [{cut: 1024, res: 12,
              route: 1, mode: 16}, end], restart: r1}
        ...
      ctrl:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0}, {wait: 1,
            note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!run:c1*3, off*3, G4!run:c1*3, ...]
        p3: [off!tempo:16*48]
    play: [p1, p2, p3]
  - def:
      ...
      x:
        x1: v + 32
        x2: v - v*(F(N) != (v + F(N)) % 65536) + (v - (v - 32)*(F(N) > (v + F(N) - 32) % 65536) - 32)*(F(N)
          < (v + F(N)) % 65536) + (v + 32)*(F(N) >= (v + F(N) + 32) % 65536)*(F(N) > (v + F(N)) % 65536)
        x3: v + 64*((φ - 3) % 12 >= 6) - 32
      phrase:
        ...
        p6: [off*3, A#3.i4!freq:x1, C4!legato!freq:x2, D4, D#4*2, C4, G4*2, C5, G#4!freq:x1, ...]
    play: [p4, p5, p6]
  - {loop: 0}
```
