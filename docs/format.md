# unitracker song format (v1.19)

A unitracker song is one YAML document. A backend lifts a native tune into it
(`unitracker-lift` reads GoatTracker 2, defMON and SID-Wizard), and the generic player
(`unitracker-play`) plays it on an emulated SID. The document is laid out so that any
prefix of it is a playable partial song: a short header comes first, then one
timeline in which every instrument, program, expression and phrase is defined in the row
where it is first used. Everything a player does is in its **playroutine**, which the song
references from the library in `src/unitracker/playroutines/`.

## Example

The start of Cadaver's *Metal Warrior Title Remix* (GoatTracker 2):

```yaml
unitracker: 1.19
playroutine: {ref: goattracker2, constants: {deftempo: 11}}
meta: {title: Metal Warrior Title Remix, author: Lasse Öörni (Cadaver), released: 2002 Covert Bitops}
hints: {ticks_per_step: 16, phrase_steps: 48}
song:
  - def:
      restart:
        r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]
      ins:
        i1: {ad: 1, sr: 136, ctrl: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 129, note: =68},
            {ctrl: 65, note: +0}, end], pw: 16*abs((φ - 32) % 128 - 64) + 512, filter: [{cut: 1024, res: 12,
              route: 1, mode: 16}, end], restart: r1}
        ...
      ctrl:
        c1: [{ctrl: 9, note: +0}, {ctrl: 9, note: +0}, {ctrl: 81, note: +0}, {ctrl: 65, note: +0}, {wait: 1,
            note: +5}, {wait: 1, note: +8}, {wait: 1, note: +0}, {loop: 4}]
      phrase:
        p1: [C2.i1*3, C4.i2, off, C3.i1, C2*3, C4.i2*3, C2.i1*3, C4.i2, off, C3.i1, G2, A#2, ...]
        p2: [off*3, G4.i3!run:@c1*3, off*3, G4!run:@c1*3, ...]
        p3: [off!tempo:16*48]
    play: [p1, p2, p3]
  - def:
      ...
      x:
        x1: (v + 32) % 65536
        x2: v - v*[F(N) != (v + F(N)) % 65536] + (-[F(N) > (v + F(N) - 32) % 65536]*((v - 32) % 65536)
          + (v - 32) % 65536)*[F(N) < (v + F(N)) % 65536] + [F(N) >= (v + F(N) + 32) % 65536]*[F(N) >
          (v + F(N)) % 65536]*((v + 32) % 65536)
        x3: (v + 64*[(φ - 3) % 12 >= 6] - 32) % 65536
      phrase:
        ...
        p6: [off*3, A#3.i4!freq:@x1, C4!legato:0!freq:@x2, D4, D#4*2, C4, G4*2, C5, G#4!freq:@x1, ...]
    play: [p4, p5, p6]
loop: 0
```

## Document

Top-level keys, in this order:

| key | content |
|---|---|
| `unitracker` | `1.19`; readers reject any other version |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (see Embedding) |
| `meta` | `title`, `author`, `released` |
| `hints` | reading aids the player ignores, each a positive integer and both optional; the complete list: `ticks_per_step`, the ticks per step as the lifter derived it, and `phrase_steps`, the common phrase length in steps when every phrase has one (the writer derives it and refuses a stated value that disagrees); readers reject any other key |
| `clock`, `voices` | only where they differ from the playroutine's `defaults` |
| `song` | the timeline: a list of rows |
| `loop` | the row the song loops to, or `~` to end |

`playroutine.constants` lists only the constants that differ from the library's.
`playroutine.tables` patches the library's tables: `{index: value}` for the entries that
differ, or a full list when the length differs.

The patch holds only entries the song can read. The writer plays the song, recording
every table index read, until its complete state (all cells, cursors and registers)
recurs. Playback is deterministic, so no later tick reads anything new, and an entry
never read cannot change the output; the library default stands in for it. If the
state does not recur within a bound, the patch is written whole.

### Embedding

`unitracker-lift --embed` writes the playroutine in full instead of `ref`, with its
`tables` complete, so the file plays without the library.

## Timeline

Each row is `{def: {...}, play: [entry per voice]}`; `def` is present only when
something is first used in that row. An entry is:

| entry | meaning |
|---|---|
| `p3` | the voice starts phrase `p3` |
| `p3+5`, `p3-2` | the voice starts phrase `p3` transposed by that many semitones |
| `end` | the voice stops |
| `~` | the voice is still playing its phrase |

A row starts at the earliest end of the voices' current phrases. Every voice whose
phrase ends there must have an entry, and every other voice must be `~`; readers
reject anything else. Repeats are written out as rows. When the voices' loops have
different lengths, the rows run until all of them are back in the same state together,
and `loop` is the row where that state first occurred.

## Definitions

`def` maps a record type, `x`, or `phrase` to `{name: value}`. Names are the record
type's prefix and a number, given in first-use order (`i1`, `i2`, ..., `c1`, ...;
expressions `x1`, ...; phrases `p1`, ...). A name is one record's or one phrase's, and the
prefixes are distinct, so a name says what it is. The reader allocates each type's indices in
definition order from the type's `first` index, so a name stands for whatever index the
playroutine needs.

Every player writes its records under the same record types, whatever its own tables are
called (each playroutine's `record_types` entry names the type it is written as, see Record
types):

| record type | prefix | holds | GoatTracker 2 | SID-Wizard | defMON |
|---|---|---|---|---|---|
| `ins` | `i` | an instrument | `ins` | `ins` | none: the pair of sidTAB programs an event arms (see the defMON backend) |
| `ctrl` | `c` | a control program: waveform, notes, and in defMON any column | `ctrltab` (wavetable) | `wftab` (WF/arp table) | `sidtab` (sidTAB) |
| `pw` | `pw` | a pulse program | `pulsetab` | `pwtab` | — |
| `filter` | `f` | a filter program | `filttab` | `flttab` | — |
| `restart` | `r` | a hard restart (see Hard restart) | `restart` | `restart` | `restart` |

A sidTAB row writes any register, so defMON's one program table is its `ctrl` programs; a
GoatTracker or SID-Wizard pulse or filter table, which drives one unit, is that unit's
program type. The transcriptions' own names are retired in v1.17 (`record type 'sidtab' is
retired in v1.17: write the record type as `ctrl``).

- **Rows** use the shared row keys (see Shared vocabulary) wherever the playroutine's
  codec can express them: `{ctrl: 129, note: =68}`. A row no codec shape covers is
  written in the type's `form`: a `list` row holds the fields in declaration order, with
  trailing defaults left out (`[4, 32]`); a `map`
  row holds the fields that differ from their defaults. A type's `retired` rows have no
  written form.
- **Programs** are the rows of a chained type, in the order they run, followed by
  `end` or `{loop: k}` (k is a row of the same program). A program is cut from the
  player's chained tables at every entry point. A tail two programs share is written
  in both, which is exact because rows are deterministic. A program that is one
  expression (see Modulators) is written as that expression:
  `pw: 16*abs((φ - 32) % 128 - 64) + 512`.
- **References.** A field that references another record holds a name. A program that
  only one instrument uses is written inline in that instrument instead
  (`ctrl: [{ctrl: 9, note: +0}, {ctrl: 65, note: +0}, end]`).
- **Expressions** (`x`, prefix `x`) are the modulator expressions an event command names
  (`!freq:@x1`): `x: {x1: (v + 32) % 65536}`. An expression no command names is written
  where it is used.
- **Fixed** record types, such as defMON's initial voice state, keep their indices and
  are defined in the first row: `vinit: {0: {note: =36, pw: 256, freq: 2227}, ...}`. They use
  the shared keys with their row meaning wherever one fits, and an omitted field is 0. defMON's
  `vinit` writes the voice's `note` (`=n`, the note its relative rows add to), `pw`, and `freq`,
  its frequency registers as the offset from that note's frequency (omitted: the note's
  frequency), and keeps the slide accumulator `sacc`; `ginit` keeps `sub`, the ACID step's
  direction (see the defMON backend).

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command:arg[!command:arg]][*steps]
```

- `note` is a note name such as `C2`, `A#3` or `C10`, `-` for no note, `off` (gate off)
  or `on` (gate on). Names are real pitch: `C2` is 65.4 Hz in every song, whatever
  index the player's frequency table gives it (see `note_offset`).
- `.i3` names the instrument. A defMON instrument names the sidTAB programs the event
  arms (see the defMON backend).
- `!run:@c1` is a named command with an argument: a number, `@name` for a command that
  references a record or an expression, `none`, or values joined by `/` for a command of
  several values (`!funk:6/7`). An event carries at most two commands,
  its two slots, then `!pulse:0` and `!filter:0` (see Lifetime), which take no slot. The
  first slot is the instrument column and the second the FX column, and an event that names
  an instrument fills the instrument column with it, so its commands are the FX column's. A
  command the player plays in one column takes either slot: SID-Wizard plays `!ad` and `!sr`
  in the column of their slot (see Backends), and its `!freq`, `!legato:0`, `!run` and timing
  commands in their own. Its FX column holds one command, so an event whose two commands both
  play there is refused, naming both (`sidwizard: the player can't play !tempo and !freq on one
  event of phrase p1`); a pair is played when one of them is an instrument-column form (`!ad` or
  `!sr` in the first slot of an event that names no instrument, `!run` on such an event) or
  `!legato:0`. GoatTracker reads `!legato:0` from either slot and its other command from the
  other, and defMON reads one command, so for them the slots are only an order. Command
  names are the ones below.
- `*3` makes the event last 3 steps; the steps after the first are empty.

## Shared vocabulary

Program rows and commands use one vocabulary in every song, in chip units.

| row key | meaning |
|---|---|
| `ctrl` | control register value; `^n` sets an exclusive-or mask the voice's control output keeps until a row sets another, `v^n` both |
| `gate` | the gate the control register is written with until a row, note or `off`/`on` sets it: 0 off, 1 on |
| `note` | `+n` / `-n` relative to the event note, `=n` absolute (format numbering, C2 = 24); omitted: no change |
| `wait` | ticks before the row applies |
| `hold` | ticks the row lasts after applying; omitted: 1 |
| `ad`, `sr` | envelope registers |
| `pw` | the 12-bit pulse width, a value or an expression; `none` stops the voice's pulse program |
| `cut` | the 11-bit cutoff over `$D415`/`$D416`, a value or an expression |
| `freq` | the voice frequency's expression; an integer is a constant offset from the note's frequency |
| `res` | the resonance, bits 4-7 of `$D417` (see Filter) |
| `route` | the filter routing, bits 0-3 of `$D417`: a mask, or an edit for the program's voice (see Filter) |
| `mode` | the `$D418` filter-mode bits 4-7, volume excluded (see Filter) |
| `run` | the program the row starts in the slot its kind implies, by name, as `!run:@x` |
| `filter` | `none`: the row stops the filter program, as `pw: none` the pulse program |
| `vol` | the volume, the low nibble of `$D418`, as `!vol:n` |

| command | meaning |
|---|---|
| `!run:@x` | start program `x` in the slot its kind implies; `!run:none` stops it |
| `!ad:`, `!sr:`, `!ctrl:` | set the register |
| `!resfilt:n` | set `$D417` whole, `n = 16*res + route` |
| `!vol:n` | set the volume, the low nibble of `$D418` |
| `!cut:n` | set the 11-bit cutoff |
| `!tempo:n`, `!vtempo:n` | n ticks per step, for all voices / this voice |
| `!funk:a/b`, `!funk:none` | swing tempo, for all voices: the steps alternate `a` and `b` ticks (each from 2); `none` swings on the pair already set (9/6 until one is) |
| `!freq:@x` | drive the voice frequency by expression `x` (see Modulators) |
| `!legato:0` | the event's note does not retrigger (see Legato) |
| `!mark:n` | a timing mark that changes no sound |
| `!restart:@r` | the note's hard restart, for a player whose instruments have none (see Hard restart) |
| `!pulse:0`, `!filter:0` | the note does not restart the instrument's pulse / filter program (see Lifetime) |

`!ad:n` and `!sr:n` are the byte the register takes. SID-Wizard sets them with a small FX
that changes one nibble of the sounding instrument's byte, so it plays a byte that differs
from that in at most one nibble (see Backends).

Each player takes the commands its playroutine declares (`commands`, `restart`, `lifetime`),
with the arguments below: a range of the written value, `@type` a record of that type, `—`
a command the player refuses by name (`defmon: the player can't express !tempo:6`).

| command | `defmon` | `goattracker2` | `sidwizard` |
|---|---|---|---|
| `!run` | — | `@ctrl`, `@pw`, `@filter` | `@ctrl`, `none` |
| `!ad` | — | 0-255 | 0-255, at most one nibble from the instrument's |
| `!sr` | — | 0-255 | 0-255, at most one nibble from the instrument's |
| `!ctrl` | — | 0-255 | — |
| `!resfilt` | — | 0-255 | — |
| `!vol` | — | 0-15 | — |
| `!cut` | — | 0-2040 step 8 | — |
| `!tempo` | — | 3-127 | 3-127 |
| `!vtempo` | — | 3-127 | 3-127 |
| `!funk` | — | 0-255/0-255, `none` | 3-15/3-15 |
| `!freq` | — | `@x`, `none` | `@x`, `none` |
| `!mark` | — | 16-255 | — |
| `!restart` | `@restart` | — | — |
| `!legato` | — | 0 | 0 |
| `!pulse` | — | 0 | 0 |
| `!filter` | — | 0 | 0 |

The timing commands mean one thing in each player that declares them: `!tempo:n` and
`!vtempo:n` make the steps n ticks long, and `!funk:a/b` alternates `a` and `b`, from the
step that carries the command as the player counts it. GoatTracker's command $F plays a
tempo of 2 as 3, so `!tempo` and `!vtempo` start at 3; `!funk:a/b` carries the swing pair, and
the default tempo is the constant `deftempo`. SID-Wizard's tempo is the subtune's tempo pair,
the constants `tempol` and `tempor`, which its FX $10, $13 and $11 set as `!tempo`, `!vtempo`
and `!funk` (see the SID-Wizard backend). A defMON step is one tick, and its arranger and
pattern counters are lifted as the timeline (see Backends).

A shared key means the same wherever it appears: in program rows, in initial-state
records, and in instruments, where `ctrl` names the program run on the control register
(GoatTracker's `ctrl: [...]` or `ctrl: c1`), `filter` the program that drives the filter, and
`restart` the hard restart (see Hard restart). A row or command a player can't express is
rejected on reading, naming the key or command and the player (`sidwizard: the player
can't express !ad:255 over the instrument's ad 18`); the writer refuses to write a value that
would not read back (a defMON pulse cell over `$0FFF`). No key, command or record type is
one player's own.

The instrument keys `gt`, `fw` (GoatTracker), `hrad`, `hrsr`, `firstctrl` and bits 0-3 of
`flags` (SID-Wizard) and defMON's `wgl` column are retired in v1.8: a document using them is
rejected, naming them (`ins.gt is retired in v1.8: write the hard restart as the
instrument's restart program`, `ctrl: wgl is retired in v1.8`). The list rows of
GoatTracker's pulse and filter tables and SID-Wizard's pulse, filter and WF tables are retired
in v1.10 (`pw: row [32, 16] is retired in v1.10: a sweep, stall or keyboard-tracked row
is its program's expression`): such a row is part of its program's `pw` or `cut` expression,
which the lifter proves or fails on (see Lifting). SID-Wizard's pulse and filter row field `k`, the
keyboard-track amount, is retired in v1.19 (`pw: k is retired in v1.19: a keyboard-tracked row
is its program's pw or cut expression in N`): the amount is a term of `N` in the program's
expression (see the SID-Wizard modulators). GoatTracker's wavetable list rows are retired in
v1.15 (`ctrl: row [242] is retired in v1.15: write the row with the shared keys`): a wavetable
command row is written with its shared keys or lifts as a row that has them, else the lift fails
naming it (see the GoatTracker 2 backend), and no program row is a list. A control mask is defMON's: GoatTracker and SID-Wizard reject it (`ctrl holds
no mask here`).
A vibrato is a `freq` expression in every player: the SID-Wizard instrument keys `vibrato`
and `vibdelay`, bits 4 and 5 of `flags` (the vibrato type) and the note-column command
`!notevib`, and the GoatTracker command `!vib`, are retired in v1.11 (`ins.vibrato is
retired in v1.11: write the vibrato as the instrument's freq expression`, `!vib is retired
in v1.11`), and the GoatTracker instrument keys `speed` and `vd` (its vibrato and the vibrato's
delay) in v1.16 (`ins.speed is retired in v1.16: write the vibrato as the instrument's freq
expression`). GoatTracker's speed table has no written form: its vibrato and slide speeds are in
the `freq` expressions and `!funk` writes its tempos, so the record type `speedtab` is retired
in v1.16 (`record type 'speedtab' is retired in v1.16`).
With bits 6 and 7 a note's `!pulse:0` and `!filter:0` (see Lifetime), the SID-Wizard instrument key `flags` has no written form and is retired in v1.13 (`ins.flags is
retired in v1.13: its bits are the instrument's restart program, its freq expression, and a
note's !pulse:0 and !filter:0`); it reads as $10, the plain vibrato type, which no
expression-driven note consults, with bits 6 and 7 from the notes. An `!freq` is
always read as the player's expression command (GoatTracker command 16, SID-Wizard FX $10),
which plays every expression; no written form selects the player's own vibrato.
A note names the pitch it sounds, so SID-Wizard's per-instrument octave shift is in the
notes: the instrument key `octave` is retired in v1.11 (`ins.octave is retired in v1.11: a
note is written as the pitch it sounds`). A note is read as the player's note with no shift,
which plays the same pitch.
SID-Wizard's nibble commands `!attack`, `!decay`, `!sustain` and `!release` and their
instrument-column forms `!iattack`, `!idecay`, `!isustain` and `!irelease` are retired in
v1.14 (`!attack is retired in v1.14: write the envelope byte as !ad:n or !sr:n, in the event
slot of its column`).
The format has no `mod` record type, no `!mod` or `!noteglide` command and no mod row
keys (`reg`, `step`, `rel`, `to`, `lo`, `hi`, `at`, `every`, `delay`, `for`); a document
using them is rejected (`record type 'mod' is retired in v1.5`, `!mod is retired in v1.5`,
`can't express !noteglide:0`). A command's argument is a byte: `!freq:@x` names one of the
first 255 expressions, else the reader refuses it.

### Control programs

A `ctrl` program's rows take the keys below in each player, in the forms given: a value,
a mask (`^n`), a routing edit (`+ m`, `-`), an expression, `none`, or a program's name. A
sidTAB row writes any register, so defMON's `ctrl` rows take the most keys.

| `ctrl` row key | `defmon` | `goattracker2` | `sidwizard` |
|---|---|---|---|
| `wait` | — | value | — |
| `hold` | value | — | value |
| `ctrl` | value, `^n` | value | value |
| `note` | value | value | value |
| `ad` | value | value | — |
| `sr` | value | value | — |
| `pw` | expression | `none` | — |
| `cut` | expression | value | — |
| `res` | value | value | — |
| `route` | value, `+ m`, `-` | value | — |
| `mode` | value | — | — |
| `freq` | expression | expression | value |
| `run` | — | `pw` name, `filter` name | — |
| `filter` | — | `none` | — |
| `vol` | — | value | — |

### Chords

A chord or arpeggio is a `ctrl` program of relative-note rows in every player, as GoatTracker
writes it: `[{ctrl: 65, note: +0}, {wait: 1, note: +5}, {wait: 1, note: +8}, {loop: 1}]`. A
chord that loops is the program's looping tail; one that ends runs into the program's next
row. An event that plays a different chord starts that program with `!run` (SID-Wizard: see
Backends). The SID-Wizard record type `chordtab`, the instrument keys `chord` and `arpspeed`,
the commands `!chord` and `!ichord` and the WF row with `a` $7F are refused
(`record type 'chordtab' is retired in v1.9`, `ins.arpspeed is retired in v1.9`).

### Filter

`$D417` is `res` (bits 4-7) over `route` (bits 0-2 voices 1-3, bit 3 the external input).
`$D418` is `mode` (bit 4 low-pass, 5 band-pass, 6 high-pass, 7 voice 3 off) over the volume
(bits 0-3, `!vol`). A key a row leaves out is unchanged.

`route` is the mask, or an edit for the voice that runs the program: `route: + m` adds that
voice and the bits of `m`, `route: - m` removes them, and `m` is left out when it is 0
(`route: +`, `route: '-'`). The space belongs to the form: `route: +8` is the mask 8.

A player's codec rejects what the player can't set:

| player | `res`, `route` | `mode` | volume |
|---|---|---|---|
| GoatTracker 2 | filter parameter rows set both, `route` as a mask | parameter rows, bits 4-6 | `!vol` |
| SID-Wizard | filter set rows set `res`; `route` is rejected | set rows, bits 4-6 | not covered |
| defMON | see below | sidTAB rows, bits 4-7 | fixed at 15; `!vol` is rejected |

GoatTracker's `!resfilt:n` stays one command: the player sets both nibbles with one code,
and `!resfilt:0` also stops the filter program. SID-Wizard writes no routing in a row: it
routes the filter to every voice whose instrument has a filter program, so the routing
depends on what the other voices play and no row value can state it. A defMON sidTAB row
writes `$D417` in one of three ways: `{res: r, route: m}` with bit 3 of `m` clear sets the
byte; `{res: r, route: + m}` with bit 3 of `m` set keeps the routing and adds the voice and
`m`; `route: '-'` removes the voice and keeps the resonance.

`cut` is the 11-bit cutoff in GoatTracker and SID-Wizard. In defMON it is the ACID
accumulator: bits 8-15 are the cutoff byte, bits 0-7 its fraction and bit 16 the carry of
the last step. The playroutine writes `$D416` as the cutoff byte plus the carry, wrapped to
8 bits, raised to the model floor when it is below the floor or above $7F, and doubled on
the SID model that needs it. The replay's offset `cp`, which it adds to the accumulator's
high byte only on the way to `$D416`, is part of the accumulator: its high byte holds the
offset, wrapped to 8 bits as the replay's add-with-carry wraps it, so the cutoff byte is
exact for every offset. A defMON ACID program, and a layer-B program that sets `cp`, is
written as its row's `cut` expression, its steps and saturation test carrying the offset as
constants; the sidTAB columns `acid` and `cp` have no written form, and `ginit` holds no
offset (the replay's init clears it and the accumulator). A `cp` on a layer-A row has no form, so the lifter
refuses it, naming the row.

The sidTAB columns `fv` (`mode`), `re` (`res` and `route`), `acid` and `cp` (`cut`) and the
row key `resfilt` (`res` and `route`) are refused (`fv is retired in v1.7`, `cp is retired in
v1.12`).

### Hard restart

A `restart` is a program (record type `restart`, prefix `r`) anchored to a note: its last
row applies on the note's first tick, and each earlier row its `hold` ticks before the row
after it. A row uses `hold`, `ctrl`, `gate`, `ad` and `sr`. GoatTracker and SID-Wizard
instruments name theirs (`restart: r1`); a defMON note names its own (`C3.c2!restart:@r1`):

```yaml
restart:
  r1: [{hold: 4, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1}, end]
```

Four ticks before the note the gate goes off with envelope $0F00; on its first tick the
control register takes $09 with the gate on. A note with `!legato:0` runs no restart, and an
instrument without `restart` has none. Each player plays the shapes its codec accepts (a
row's place in its program is part of its shape, see Codecs):

| player | lookahead row | first-tick row |
|---|---|---|
| GoatTracker 2 | exactly one: `hold` 1-63 (the gate timer); `gate: 0` or nothing; with `gate: 0`, `ad`/`sr` equal to the `adparam` constant or neither | `{ctrl: n, gate: 1}` (n $01-$FD, the first wave), `{gate: g}`, or `{}` |
| SID-Wizard | at most one: `hold` 1 or 2, `gate: 0`, `ad`, `sr`, and `ctrl: 24` for a staccato restart (the voice's tables pause) | `{ctrl: n}` (the first waveform), or `{}` |
| defMON | one or more: `hold`, `ctrl` (a value, a mask `^n`, or both), `ad`, `sr` | `{}` |

GoatTracker reads the voice's next step its instrument's gate timer ahead of each note, and an
instrument without a restart reads it on the note's first tick. A GoatTracker note keeps the
gate as the restart left it, so its first-tick row sets it; a SID-Wizard note sets it itself.

defMON has no instrument restart: composers run a short sidTAB program on layer B on the
empty steps before a note. The playroutine declares this with `restart: {command: 1}`, and
the format writes it as the note's restart. On reading, a note's `!restart:@r` becomes a
layer-B start (the event command an instrument's `filter` is) of a sidTAB program of the
lookahead rows (the last one frozen, holding until the note), on the step their holds put
before the note, which must be empty; the writer folds every such start back, before it names
the events' instruments: one alone on its step, followed only by empty steps up to a note
with a free command slot, running a program that ends, sets no column but `dl`, `wgh`,
`wgl`, `ad` and `sr`, and fits in the steps. The note's own sidTAB program starts on its
first tick, so the restart's last row is empty. A step is one tick in defMON, so `hold`
counts both.

### Lifetime

Whether a note restarts its instrument's pulse or filter program is a property of the note:
`!pulse:0` and `!filter:0` mean it does not, as `!legato:0` means it does not retrigger. A
note that names no instrument plays the voice's last one; the reader finds it by walking
each voice's plays twice through its loop. The playroutine declares the commands it plays
(`lifetime: {pulse: {field: pw, flag: {flags: 0x40}}, ...}`, `field` the instrument key of
the program, `flag` the header bit that keeps it), and each player maps them as follows:

| player | `!pulse:0`, `!filter:0` |
|---|---|
| SID-Wizard | control bits 6 and 7 of the instrument header: the writer puts the command on every note that starts an instrument with the bit set and names none; the reader sets the bit from those notes, and refuses an instrument some of whose such notes carry it and others not, and a note that names an instrument, which restarts both programs |
| GoatTracker 2 | the absence of a pointer: a note starts the instrument's `pw` and `filter` programs, and its wavetable `run` rows ($F9, $FA) start them where the composer put them; the reader accepts the command on a note whose instrument has neither (and that runs no such program itself), the writer leaves it out |
| defMON | refused: an instrument's programs start on every event that names it |

A note played under instruments of which some keep a program and others restart it has no
written form. A legato note restarts nothing, so the command adds nothing there.

## Modulators

A modulator drives one cell of the player (a voice's frequency, its pulse width, or the
filter cutoff) by an integer expression evaluated at each of the unit's firings.

### Grammar

| form | meaning |
|---|---|
| `12` | an integer |
| `a + b`, `a - b`, `a * b`, `-a` | integer arithmetic, unbounded |
| `a // b` | floored division: rounds toward minus infinity |
| `a % b` | floored modulo: the result has the sign of `b` |
| `abs(a)`, `min(a, b)`, `max(a, b)` | |
| `[a op b]` | Iverson bracket of one comparison: 1 when it holds, else 0; `op` is `==`, `!=`, `<`, `<=`, `>` or `>=` |
| `v` | the cell's value before the firing |
| `φ` | the firings since the modulator started, from 0 |
| `N` | the voice's sounding note, format numbering (C2 = 24) |
| `F(k)` | the player's frequency word of format note `k` |
| `T[k]` | entry `k` of a table `T` the playroutine declares (`modulators: tables:`); 0 outside its size |

Anything else is rejected on reading, naming the text (`... is outside the grammar`,
`... calls no grammar function`, `[...] is not one comparison`, `unknown table 'T'`).

### Evaluation

Each firing of an active expression `f` computes, from the state before it,

```
v' = f(v, φ, N)        stored in the cell, wrapped to its width
φ' = φ + 1
```

A start opens an occurrence at the unit's next firing, with `φ = 0` and `v` the cell's
value then; it runs until the unit's next start. An expression with no `v` is a closed
form in `φ`: the value after firing `φ`. A plain integer in an expression slot is a
constant expression. Values a modulator carries from earlier playback (a slide's start
frequency, a delay counter, an ACID direction) are constants of its expression, which
the lifter resolves at the modulator's start. A start that selects no expression leaves
the unit to the playroutine's own firing.

### Units

| key | the unit's value |
|---|---|
| `freq` | the 16-bit voice frequency word (GoatTracker); in defMON and SID-Wizard, the 16-bit offset the player adds to the note's frequency |
| `pw` | the pulse width: 12 bits (GoatTracker); the 12-bit register (SID-Wizard: the pair `pwhi:pwlo` plus its row's keyboard-track term); a 16-bit cell whose low 12 bits are the register (defMON) |
| `cut` | the 11-bit cutoff (GoatTracker; SID-Wizard: the cell plus its row's keyboard-track term); defMON's 17-bit ACID accumulator, the offset `cp` in its high byte (see Filter) |

A unit with `scale: s` keeps its value in a cell that holds the chip value divided by
`s`: the player evaluates the expression with `v` as the cell times `s` and stores the
result floor-divided by `s`. GoatTracker's filter cell holds the top 8 bits of the cutoff
(`scale: 8`), so its expressions are in cutoff units like the other players'.

### Where expressions go

| place | players |
|---|---|
| a program row's `pw`, `cut` or `freq` | defMON sidTAB rows (`freq`, `pw`, `cut`); GoatTracker filter parameter rows and SID-Wizard filter set rows (`cut`); GoatTracker wavetable step rows (`freq`) |
| an instrument's `pw` or `filter` program, written as the one expression | GoatTracker, SID-Wizard |
| an instrument's `freq`: the pitch expression a note starting the instrument runs | GoatTracker, SID-Wizard |
| an event's `!freq:@x1`, naming an `x` definition | GoatTracker, SID-Wizard |

A key that also takes a plain value (`cut` in a GoatTracker or SID-Wizard filter row)
reads an integer as that value and a string as an expression (`cut: '1024'` is the
constant expression, written back quoted); elsewhere an integer is a constant expression. An expression that contains a comma is quoted inside a YAML flow
map: `pw: '2*min(64, φ) - 8*min(64, max(0, φ - 65)) + 2048'`.

### Legato

`!legato:0` makes the event's note sound without retriggering it: no gate-off, hard
restart or instrument start. GoatTracker writes it on a toneporta note (the format's
command 17); SID-Wizard writes instrument column $3F.

### GoatTracker 2

| unit | key | cell | fires | starts | written |
|---|---|---|---|---|---|
| `command` | `freq` | `freq` | every tick of a step but tick 0, except ticks a wavetable row plays a note or runs a command | the step's command or argument changes, a note starts, or a wavetable row plays a note | `!freq:@x` (command 16); without a command, the instrument's `freq` |
| `pulse` | `pw` | `pulse` | each pulse-program tick: not on the tick the voice reads its next step, nor tick 0 of a step that starts a phrase | an instrument, `!run:@pw` or a wavetable `$F9` sets the program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `filtercutoff`, `scale: 8`, global | each filter-program tick | an instrument, `!run:@f`, `!resfilt:0` or a wavetable `$FA`/`$FB` sets the program | the program's first row (instrument `filter`) |
| `wave` | `freq` | `freq` | once, on the tick its wavetable row runs | every run of a wavetable step row | the row's `freq` |

A `!freq` lasts its step. The same `!freq` on the next step continues the occurrence
(`φ` keeps counting); a different one, or a step with another or no command, starts the
next. A step without a command runs the instrument's `freq` expression, which a note's steps
without a command continue. Commands 1-4 and the instrument vibrato are the unit's own
firings; a lifted song writes the commands' modulations as `!freq`, and the instrument
vibrato as the instrument's `freq`, its delay a term in `φ` (`[φ >= 38]` for a delay of 40),
when it starts with the note; a vibrato that resumes after a command, or after a row that
selects another instrument without a note, is `!freq` on each of its steps. An instrument
with a vibrato delay of 0 has no vibrato, whatever its speed, as the player packs it.

A wavetable row's `freq` is one firing: the portamento, toneporta or vibrato step ($F1-$F4)
the row runs, with its speed and the voice's state at the run resolved (a realtime speed reads
the note the frequency base came from, a term of `N`). It is read as a portamento step of
speed 0, which leaves the frequency alone, so the step sets nothing but the frequency: a row
whose runs need different expressions (a vibrato step met on different phases) fails the lift,
naming the row; one whose step's other effects (a vibrato's phase, a toneporta's arrival) the
song hears fails it too.

### SID-Wizard

| unit | key | cell | fires | starts | written |
|---|---|---|---|---|---|
| `pitch` | `freq` | `voff` | every tick but the one a note starts on | a row writes `voff` or changes the vibrato or slide state, at the row or at its deferred FX | the instrument's `freq` for an instrument vibrato; else `!freq:@x` (FX 16) |
| `pulse` | `pw` | `pwhi:pwlo`, as the register: plus the row's keyboard-track term | every tick but the one a note starts on | a note starts the pulse program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `vfhi:vflo:3`, mirrored to the global cutoff, as the cutoff: plus the row's keyboard-track term | every tick the voice controls the filter but the one a note starts on | a note starts the filter program | the program's first row (instrument `filter`) |

A `!freq` lasts until a note starts the instrument (which selects its `freq`) or another
pitch effect replaces it. Big FX $01-$03, note-column vibrato ($60-$77) and glide ($78)
are the pitch unit's own firings; a lifted song writes their modulations as `!freq`.
Speeds read the table `W[k]` (`exptab[k]` below 107, the frequency word of note index
`k - 107` above, size 204). A pulse row's keyboard-track amount `k` (its third byte) adds a
term of the note to the register's high nibble from the row until a later row's amount
replaces it: with `y` the sum `N + 1 + k` wrapped to a byte and `c` its carry, `256·(E[y] -
E[y - 1] + c - [E[y] - E[y - 1] + c < 1])` over the table `E[i]`, the player's EXPTABH (0 below 11,
then the frequency high bytes from index `i - 11`, size 256). A lifted program writes it as
its `pw` expression in `N`. The filter program runs for the voice that controls the filter,
and a filter row's amount adds `E[y] + c` to the cutoff's high byte, with `y` and `c` from the
controlling voice's note, from the row until a later row's amount replaces it: a lifted
program writes it as its `cut` expression in `N`. A sum past $FF also adds its carry to
`$D416`, which no cutoff value states, so a song that reaches it fails the lift; a third byte
of $80-$8F sets the routing instead, which the lifter does not cover.

### defMON

| unit | key | cell | fires | starts | written |
|---|---|---|---|---|---|
| `pitch A` | `freq` | `sacc`; with `af` below $80, the offset `S[k]` it adds | every tick, from layer A's row to the pitch step | a note, or layer A armed with a program that sets `af` or `freq` | the program's first row (it runs the slide mode $80, and the program's other rows set no `af`) |
| `pulse A` | `pw` | `pw` | every tick, from layer A's row to the pulse step | layer A armed with a program that sets `pw`, `ps` or a pulse expression | the program's first row, replacing its `pw` and `ps` columns |
| `acid B` | `cut` | `acc`, global | every tick, from layer B's row to the next ACID step | layer B armed with a program that sets `acid`, `cp` or a cutoff expression | the program's first row, replacing its `acid` and `cp` columns |

`af` below $80 adds a fixed offset to the note's frequency: the step `S[k]` (the table
`S`, size 256) of note `k` = `N + 24 + af`. A lifted program writes it as its first row's
`freq` expression, `S[N + 36]` for `af` 12, which plays in the slide mode with the same
offset; the sidTAB column `af` is retired in v1.11 (`ctrl: af is retired in v1.11`).
A program that changes the pitch mode after its first row, or sets `af` below $80 under
relative-note rows, is not covered: the slide mode drops the voice's detune before the
change, and the offset of a relative note reads the note base.

## Canonical form

The writer produces one text per song:

- names are given in first-use order, each a record type's prefix (distinct from the others' and
  from the phrases' `p`) and a number;
- records and rows the song never reaches are dropped;
- identical phrases, and phrases that are equal up to a transpose, are merged, and
  the transpose moves into the play entry;
- equal expression texts share one definition;
- runs of empty steps become `*steps`;
- a playroutine's event restarts are written on their notes (see Hard restart);
- a phrase whose envelope bytes differ between the places it plays is split into one
  phrase per place (see the SID-Wizard backend), the inverse of the merge;
- no-op commands are dropped.

Writing what the reader returns gives the same bytes.

## The playroutine

The library holds one definition per player build:

```yaml
name: str
constants: {name: int}
defaults: {clock: {cycles_per_tick: 19656, clock_hz: 985248}, voices: 3}
onset: started   # the voice cell that is 1 once the voice's registers hold its first note
tables: {name: [ints]}                    # player-build data; reads out of range give 0
note_offset: 0                            # frequency-table index of format note 0
instrument: {type: ins, base: 0}          # the record type .name refers to; index = value - base
restart: {command: 1}                     # event restarts, played by this command (see Hard restart)
lifetime: {pulse: {field: pw, flag: {flags: 0x40}}}   # !pulse:0 and !filter:0 (see Lifetime)
exclusive: "code < 0x100"                 # over code and instrument: commands of a one-command column
commands: {8: {name: run, ref: ctrltab}, 15: [{name: tempo, when: ..., arg: ..., raw: ...}], ...}
record_types: {name: {...}}
functions: {name: {args: [a, b], expr: "..."}}   # inlined into expressions
global: {cell: {width: 8, init: 0}}       # width omitted: unbounded
voice:  {cell: {width: 8, init: 0}}       # one copy per voice
records: {name: {index: {field: ...}}}    # playroutine-owned, e.g. command dispatch
procs: {name: [actions]}
init: [actions]                           # first tick only, global scope
tick: [actions]                           # every later tick, global scope
ghost:
  global: {0x15: expr, 0x16: expr, 0x17: expr, 0x18: expr}
  voice: {0: expr, 1: expr, ..., 6: expr} # offsets within a voice
modulators: {freq: ..., notes: [...], units: [...]}   # see Modulator declarations
```

### Record types

| key | meaning |
|---|---|
| `fields` | a list of names, or `{name: default}`; a missing field reads as its default |
| `form` | `list` (default) or `map` |
| `writes_as` | the record type a document writes it as (default: its own name; see Definitions) |
| `prefix` | the name prefix (default: the first letter of the written name); distinct from the other types' and from the phrases' `p` |
| `chain` | the field holding the next row: the type is a table of programs |
| `none` | the index that means "no row"; references equal to it are plain numbers |
| `stop` | an expression over a row's fields after which a program ends |
| `first`, `max` | the indices the reader allocates (default `none + 1`, or 0; up to 255) |
| `pin` | indices the playroutine reads directly; they are defined first |
| `fixed` | the rows keep their indices |
| `refs` | `{field: type}`, or `{field: [{type: t, when: expr}, ...]}` for a reference that depends on the row |
| `codec` | shapes writing rows as shared keys (see Codecs) |
| `end` | raw fields set on a program's last row when it is followed by `end` |
| `expr` | the field (a reference to `x`) holding a row's modulator expression; a program of one row that sets only it is written as the expression |
| `retired` | `{expression: reason}`: raw rows no codec shape covers that the format no longer has; the reader and the writer refuse such a row for which an expression holds |
| any other key | a template: an action list run for a row |

The record type `x` is the format's own: it holds the expressions, and a playroutine
cannot declare it.

A template reads the row it runs for as `row.<field>`. `run: {record: r, index: i,
field: f}` runs record type `r`'s template `f` for row `i` when that row exists.
GoatTracker's control-program rows, for example, decode their bytes in the template:

```yaml
ctrltab:
  writes_as: ctrl
  none: 0
  chain: next
  fields: [w, n, next]
  do:
    - if: "row.w >= 0xF0"
      then: [{set: {arg: row.n}}, {run: {record: wavecmd, index: "row.w & 0x0F", field: do}}, ...]
    ...
```

### Codecs

A codec shape has a `when` over the raw fields, the playroutine's constants, and for a
program row its place: `first` and `last` are 1 for the program's first row and for the
row before its `end`; `keys` (`{key: {value, omit, abs, expr, eor}}`:
the shared key's value from the raw fields, when to leave it out, for `note` whether
it is absolute, the raw field holding the key's expression, and for `ctrl` its mask as
`{value, omit}`), `raw` (each raw field from the written keys, with `has_<key>`,
`note_abs`, and for a mask `has_eor` and `eor` defined) and `keep` (raw fields
written under their own names; a kept reference is written as a name). A key with `expr`
is written as the expression when that field is set; a key with both `value` and `expr`
reads an integer as a value and a string as an expression. A key given as
`{ref: type, field: f}` is the reference raw field `f` holds, written as the record's name
(`run: pw1`); the reader takes the shape whose `ref` is the type the name has. A key given as
`{none: true}` is written `none` (`pw: none`) and reads only from `none`. The writer
uses the first shape whose `when` holds and refuses a row that would not read back; a `map` row no
shape fits is an error. The reader accepts a written row only if encoding its inverse
gives the row again. A command entry has a `name`, an optional `ref` (a record type, or
`x` for an expression), and for a plain argument optional `when`, `arg` (raw to written)
and `raw` (written to raw), checked the same way; `args` (`{a: expr, b: expr}`) writes the
argument as several values joined by `/`, which `raw` reads by those names (SID-Wizard's
`!funk:6/7`). A `ref` entry with `inline: true` writes
the referenced row's fields, each a byte, joined by `/` (`!funk:6/7`); the reader allocates
one row per distinct argument, and the type has no definitions of its own. An entry `{name: ad, nibble: s, slot: k}`
is an envelope command: the code sets the nibble at shift `s` of the register `name` over
the instrument's field `name`, played from event slot `k`, and is written as the byte it
writes (see the SID-Wizard backend).

A raw field given as `{search: [lo, hi]}` is the first value in that range whose row
writes back as the written one. Codec expressions may read the song's tables and the
playroutine's functions. A list row a shape covers is rejected: it has a shared-key form.

### Cells and expressions

Every assignment wraps to the target cell's `width`, using two's complement for
negative values. Expressions use Python syntax, restricted to:

- integers;
- names, which resolve as a voice cell (in voice scope), then a global cell, then a constant;
- `table[i]`, `record[i].field` and, in a template, `row.field`;
- `+ - * // % & | ^ << >> ~`, comparisons, `and`, `or`, `not`, and `a if c else b`;
- calls to `functions`.

Arithmetic is unbounded; only assignment wraps. These are the playroutine's
expressions; a song's modulator expressions use the grammar of Modulators.

### Actions

| action | meaning |
|---|---|
| `set: {cell: expr, ...}` | Assign in order, each wrapped to its cell's width. |
| `if: expr`, `then: [...]`, `else: [...]` | Conditional. |
| `call: proc`, `with: {cell: expr}` | Assign the `with` cells, then run the proc. |
| `run: {record: r, index: expr, field: f}` | Run a record type's template, or the action list stored in a record field. |
| `for_voices: [...]` | Run the list once per voice, in voice order, with that voice's cells bound. |
| `fetch: true` | Read this voice's next step (see The cursor). |
| `return: true` | Leave the innermost proc, template, record action list, `for_voices` body, or `init`/`tick`. |
| `accumulate: cell` | The bounded accumulator. |

### The bounded accumulator

```yaml
accumulate: cell
step: expr             # signed; with toward, a magnitude
min: expr              # optional bounds
max: expr
toward: expr           # optional target: approach it from either side
signed: false          # compare as two's complement at the cell's width
clamp: true | false | reflect
on_bound: [actions]
```

```
v0 = cell
if toward given:
    if v0 == T: done
    if v0 < T: s = step, max = T  else: s = -step, min = T
else: s = step
v  = wrap(v0 + s, width(cell))           # bounds are tested after wrapping, as players do
hit = v > max (if max given) or v < min (if min given)   # signed: compared as two's complement
if hit and clamp is true: v = the bound crossed
if hit and clamp is reflect: v = wrap(v0 - s, width(cell))
cell = v
if hit: run on_bound
```

With `signed`, `v0`, `v`, `min`, `max` and `T` are read as two's complement at the cell's
width (`x - 2**w` when `x >= 2**(w-1)`), including the `toward` comparisons `v0 == T` and
`v0 < T`; the stored value still wraps to the width. `signed` needs a cell with a width, and
`clamp: reflect` cannot be combined with `toward`. A reflecting direction is an ordinary
cell inside `step`, flipped in `on_bound`:

```yaml
accumulate: freq
step: "vspd if vd == 0 else 0 - vspd"
min: lo
max: hi
clamp: reflect
on_bound: [{set: {vd: "vd ^ 1"}}]
```

### Modulator declarations

`modulators` declares the player's units and what their expressions read.

| key | meaning |
|---|---|
| `freq` | the word `F(k)` reads at index `k + note_offset`: `{word: table}`, or `{hi: table, lo: table}` joined as `hi << 8 \| lo` |
| `notes` | the voice cells that hold the note; `N` is the first, less `note_offset` |
| `transpose` | a voice cell added to the note for `N` |
| `legato` | `{cell, command}`: the cell set while a note does not retrigger, and the command code written as `!legato:0` |
| `tables` | `{name: {entry: expr, size: n}}`: the tables `name[k]` expressions read; `entry` reads `i` and the playroutine's tables |
| `functions` | `{name: expr}`: playroutine functions rewritten over the named tables, for analysis |
| `units` | the units, below |

| unit key | meaning |
|---|---|
| `name` | the unit's name |
| `key` | the shared key it drives: `freq`, `pw` or `cut` |
| `cell` | the driven cell; a pair `hi:lo[:k]` is the value `hi << k \| lo & (2**k - 1)` (k 8 when omitted) |
| `chip` | `{width, value, store}`: the unit's value is the chip value `value` reads from the cells, `width` bits, which `store` (reading `value`) writes back; on a pair cell it is the pair plus an offset free of the pair's parts |
| `scale` | the chip value per cell unit (default 1; see Units) |
| `scope` | `global` for one cell shared by the voices, owned by the voice whose start opened it |
| `fire` | the actions (or proc names) of one firing |
| `pre`, `post` | the functions that bound one firing (`post` defaults to `pre`): `v` is read before `pre` runs, the expression's value is stored after `post` |
| `x` | an expression over the voice's cells giving the expression a start selects; 0 leaves the playroutine's own firing |
| `hooks` | the starts: `{at: function, written: [cells], key: [cells], pointer: [cells]}`; the unit restarts when the function writes a `written` cell, changes the `key` cells, or writes a `pointer` to a program that sets one of `program.fields` |
| `program` | `{pointer, record, fields}`: the program a `pointer` selects and the fields that make it a start |
| `mirror` | a cell that takes the cell's value an expression stores when it held the cell's own |
| `label` | the cells that group a unit's occurrences for the lifter |
| `relative` | cells a start reads as `N` plus their offset from the note (GoatTracker's `lastnote`) |
| `site` | where the lifter writes the expression: `{record, index, field, clear, first, when, row}` (`clear`: fields the expression replaces; `first`: fields set on the program's first row; `when`: over the start's cells, else the event's command; `row`: the site is the program row the start runs, which holds the expression of its one firing) |
| `retire` | `{record, clear}`: native fields the unit's expressions replace and the format does not write; the lifter clears them in every row of `record`, and the replay proves the song unchanged |
| `command` | the command code that names an expression (`!freq`) |
| `commands` | the native command codes the lifter replaces by `command` |

A function is named `procs.<proc>`, `records.<record>.<field>[.<index>]` or
`record_types.<type>.<template>`.

### The cursor

The player keeps one cursor per voice and the playroutine decides when it moves.
`fetch` reads the voice's next step and sets these voice pseudo-cells:

| cell | value |
|---|---|
| `ev_kind` | 0 empty, 1 note, 2 off, 3 on |
| `ev_note` | note + transpose |
| `ev_instr` | instrument index + `base`, or 0 when absent |
| `ev_cmd`, `ev_arg` | first command and argument (an index for a reference), or 0 and 0 |
| `ev_cmd2`, `ev_arg2` | second command and argument, or 0 and 0 |
| `at_phrase_start` | 1 when the next `fetch` reads step 0 of a phrase |
| `ended` | 1 once the voice has stopped |

### Ticks, the ghost and the canonical flush

Each tick, the player runs `init` (first tick) or `tick` (every later tick). Then it
evaluates the ghost, masks each value to 8 bits, and flushes the registers that
changed since the last flush, in GoatTracker 2's buffered order (`BUFFEREDWRITES`:
the filter step first, then `mt_loadregs`). A register with no ghost entry keeps its
value.

```
$15 $16 $17 $18,  then for each voice v (base 7v): +2 +3 +6 +5 +0 +1 +4
```

### Checking against the native player

The player starts from a zeroed SID. `unitracker-play --check` compares a voice's seven
registers with the native player's from the tick the voice's `onset` cell first reads 1,
and the filter and volume registers (`$D415`-`$D418`) from the earliest onset. What a
native player writes before then is its own initialisation, not the song's, and the
format does not carry it: a song's text carries only what the composer's rows and
definitions imply.

## The internal representation

`unitracker.format.songio.loads` expands a document to the dict `SongPlayer` runs
(`"unitracker": "ir"`): numbered records with a `next` field per program row,
per-voice tracks of plays, phrases of event dicts, the expressions as
`exprs: {index: text}`, and the full playroutine and tables. Lifters produce this
representation and `songio.dumps` writes it.

## Lifting

The lifters (`backends.lift`, `goattracker.lift`, `defmon.lift`, `sidwizard.lift`) write
each native modulation as an expression by default; `expressions=False` returns the
internal representation with the native modulations. The conversion
(`unitracker.modfit.convert`) replays the song to the recurrence of its state and one
period more, derives and proves each occurrence's expression from its start, and writes
it at the unit's site. Within a unit and label the occurrences share one closed form,
else one recurrence, else each start keeps its own. The rewritten song must play the
native register grid exactly over that window, each voice from its first note (see Checking
against the native player); otherwise the lift fails. A start whose
expression is not proved fails the lift, naming the unit, its program and the start
(`pulse program pulsetab 3, start [pptr 3] at tick 40: not proved: ...`), and so does a
pulse or filter program the song references whose rows keep no shared form because no
start proves its expression.

## Backends

| backend | tune | step | lifted from |
|---|---|---|---|
| `goattracker2` | packed GoatTracker 2 `.sid` (pygoattracker) | one pattern row | the decompiled song and the build's constants |
| `defmon` | defMON `$1000` replay `.sid` (pydefmon) | one tick | the replay's memory after init |
| `sidwizard` | SID-Wizard 1.x export, normal build (pysidwizard) | one pattern row | the decoded module |

**GoatTracker 2.** The playroutine counts ticks per row itself, and `fetch` runs on its
gate-timer lookahead, the hold of the restart's first row. A voice's `onset` is the tick a
wavetable row first sets its frequency: on its first note's first tick the voice still sounds
the frequency it had before the note, which the format does not carry. Instruments with one gate timer and
first wave share a restart program; a hard restart on gate timer 0 is not covered. Instrument 1 is pinned because every voice starts on it.
The filter step writes `$D416`-`$D418` before the channels run, so the playroutine latches
them there (`fcutreg`, `fctrlreg`, `fmodereg`, which the ghost reads), and a pattern command
or wavetable row that sets the cutoff, `res`/`route` or the volume ($B-$D, $FB-$FD) sounds on
the following tick. A
filter parameter row carries the following cutoff row's value as `cut`, because the
player reads both in one tick. A wavetable delay row is `wait` on the row it delays. A
wavetable command row runs the pattern command of its low nibble, and the tick's continuous
effect does not run, as on a note row; the commands with a shared form are written with its key:
$F5 `ad`, $F6 `sr`, $F9 and $FA `run` (the pulse or filter program), and with 00 `pw: none` and
`filter: none` (the program stopped), $FB `res` and `route` (00 also stops the filter program:
`{res: 0, route: 0, filter: none}`), $FC `cut` and $FD `vol` (below $10). A
wavetable jump is the program's `loop`. $F1-$F4 (one portamento, toneporta or vibrato step)
are the row's `freq` (see Modulators). $F7 sets the waveform as a waveform row does, but also
pauses the tick's continuous effect; the lifter writes it as the waveform row of its value
(`ctrl`) when a replay to the song's recurrence and one period more proves the pause unheard,
and otherwise fails, naming the rows and the tick that hears it, as it does for a value from
$E0, which no waveform row sets. $F0, $F8 and $FE, on which the playroutine stops, are not
lifted: the lifter names the row. $FD $10-$FF does nothing but pause the tick's continuous
effect, and the lifter writes it as the empty row `{}`, proved as for $F7; it has no written form
of its own (`{vol: 16}` is refused).

**defMON.** The arranger and pattern counters are lifted as timing. Each song step
becomes one phrase per voice, all of the same length in ticks, with each event on the
tick the replay applies it. The playroutine fetches every tick and maps the event
fields as follows:

- `.i3` names the instrument: its `ctrl` is the sidTAB program sidcall layer A starts (the
  event's instrument column), its `filter` the one layer B starts (the event's command), and a
  layer the instrument leaves out keeps running;
- `!restart:@r1` arms layer B on the steps before the note;
- the note is a GATE_N note.

The writer names one instrument per pair of programs the events arm (`{ctrl: c1, filter: c2}`,
`{filter: c2}` on an event that arms only layer B), so `C2.i4` and `-.i5` are a note and an empty
step arming them. Layer B is the filter's: its ACID step drives the cutoff, and only a layer-B
row's `cut` is the ACID unit's (see Filter). An instrument holds no other key (`ins: unknown
field 'pw'`), arms at least one program, and the command `!run2` is retired in v1.17
(`!run2 is retired in v1.17: name the program as the `filter` of the event's instrument`).

sidTAB rows are maps of the columns present, applied in the replay's bitmap-walk
order: `dl` is `hold` (a frozen row ends its program), `wgh` and `wgl` are `ctrl` and its
mask (`ctrl: 64^1`), `tr` is
`note`, the row's pitch, pulse and ACID modulators are `freq`, `pw` and `cut` (the ACID
offset `cp` part of `cut`, see Filter), and the other columns keep their names. Note names are offset by 24 indices: defMON index 48
is `C2`. The replay probes `$D41B` at init to tell the SID models apart; the result
becomes the playroutine constants `floor` and `dbl`. The ghost masks pulse-width high
to a nibble, as the reference grid does.

The replay's init ($1522) clears `$D417`, `$D418`'s filter bits, the ACID accumulator and step,
and each voice's envelope, control register and mask, pitch mode, pulse step, sounding note and
layer rows, and freezes both layers; the playroutine's init does the same, and the lifter
refuses a tune whose init leaves another value, naming the cell. What the module carries
through init is the song's initial state: each voice's pulse cell, frequency registers, slide
accumulator and the note its relative rows add to (`vinit`: `pw`, `freq`, `sacc`, `note`), and
the ACID step's direction (`ginit`: `sub`, 1 when the replay's ACID step subtracts, its SBC, and
0 when it adds, its ADC), which the replay's own ACID firing reads until a layer-B program sets
the cutoff. The replay's names for these and the cells its init sets are retired in v1.17
(`vinit.tbase is retired in v1.17: write the voice's note as `note``, `vinit.cntA is retired in
v1.17: the replay's init sets it`). The frequency registers sound only on a voice whose first
note is on the first tick, which latches them before the note; the slide accumulator only under
a slide a layer-A program starts before the voice's first note.

**SID-Wizard.** The playroutine runs each voice's tempo counter, reads a row at TICK_0,
applies it at TICK_2 and plays the next instrument's restart on the ticks between, ticking
the voices in the player's reverse order. An instrument is its header (`ad`, `sr`),
its WF/arp, pulse, filter and restart programs (`ctrl`, `pw`, `filter`,
`restart`) and its pitch expression (`freq`), which holds the header's vibrato; a
note-column vibrato is `!freq`. The header's control bits 6 and 7 are its notes' `!pulse:0`
and `!filter:0` (see Lifetime). The header's octave shift moves into the notes the
instrument plays, each written as the pitch it sounds; a row that selects an instrument of
another shift without a note, and a phrase note played under instruments of different
shifts, are not covered. The header's hard-restart bytes and control bits 0-3 become the
restart program; a restart on both ticks before the note (control bits 0 and 1) is not
covered. WF rows use `ctrl`, `note`, `hold` and `freq`.
`hold` is the ticks the row lasts: on a row with `ctrl`, the instrument's arp speed plus one;
a row without `ctrl` keeps the waveform and lasts its native hold count plus one (1-16). A
program that ends paces itself afterwards by its last row's `hold`, so a hold row ending a
program at another arp speed is not covered. `freq` is the row's detune, a constant from -128
to 126 the player adds to the frequency word from that row until a later row sets another
(a row without it sets 0; a native row that keeps the detune is written with the one it
keeps, and the lifter refuses one that keeps different detunes on its loop's passes). Pulse
set rows use `pw`, and a program with a keyboard-tracked row is its `pw` expression in `N` (see
Modulators); filter set rows `mode`, `cut` and
`res`, and a program with a keyboard-tracked row is its `cut` expression in `N`. A WF row with `a` $7F plays the chord: the lifter inlines the chord's offsets there,
each row with that row's waveform, which the player writes on every chord step. A chord
small FX ($7x) holds until the voice's next note, so on a note it becomes `!run` of the
instrument's program with that chord inlined. `!run` plays in the instrument column on an
event that names no instrument and in the FX column otherwise: the player times the two
differently on a note's hard-restart ticks. A chord FX on a row that starts no note, and an
FX-column chord on a note that names no instrument, are not covered. An envelope small FX
($2x-$6x: attack, decay, sustain, release; $12x-$16x from the instrument column) sets its
nibble's override, which clears the other nibble's of that register and lasts until the
voice's next note start, and writes both registers, each nibble from its override, else from
the instrument; on a row that starts a note both columns run a tick later, the instrument
column's first (an FX-column small FX ends the tick's FX), and the instrument column's also
sets the carry into the frequency low byte on the ticks before the row applies while the WF
program holds a row. The writer writes each as the byte it writes, `!ad:n` or `!sr:n` in the
slot of its column, from the voice's instrument, found by walking each voice's plays twice
through its loop; the reader plays each byte as the small FX that changes one nibble of the
instrument's byte (the attack or sustain nibble when the byte is the instrument's). A phrase
whose bytes, or small FX, differ between the places it plays becomes one phrase per place. An
FX-column one alone on a row that starts no note is written in the first slot, which plays the
same. Not covered: one on a voice with no instrument, one that writes the other register with
another byte (after a row that selects an instrument without a note), an FX-column one alone
on a note that names no instrument, and a play of a phrase that differs on its loop's passes.
The tempo FX apply on the
row's TICK_2 with its other FX: $10 (`!tempo`, playroutine code $12, as $10 is `!freq`) sets
every voice's tempo pair to its value, $13 (`!vtempo`) this voice's, and $11 (`!funk:a/b`)
every voice's to its nibbles. Each rewinds the swing to its first tempo, and each voice
counts its current row against the new pair from its next tick. A tempo of 0 plays as 6, and
1 and 2 never apply a row, so they are not covered, nor is a swing for one voice ($14) unless
both its tempos are equal, which is `!vtempo`; the lifter names the pattern and row of a tempo
FX it does not cover. Note names are offset
by one index: SID-Wizard index 49 is `C4`. The lifter
requires each voice's first note to select an instrument: a note that selects none on a
voice that has never had one has no instrument state to play. Table jumps, the orderlist's
transpose, tempo and volume commands,
multispeed, other player builds and the effects not listed above are not covered yet;
the lifter names what it meets.
