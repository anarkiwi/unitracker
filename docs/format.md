# unitracker song format (v1.11)

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
unitracker: 1.11
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
      ctrltab:
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
| `unitracker` | `1.11`; readers reject any other version |
| `playroutine` | `{ref: name, constants: {...}, tables: {...}}`, or the full definition (see Embedding) |
| `meta` | `title`, `author`, `released` |
| `hints` | derived facts, such as `ticks_per_step` and `phrase_steps`; the player ignores them |
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
type's prefix and a number, given in first-use order (`i1`, `i2`, ..., `w1`, ...;
expressions `x1`, ...; phrases `p1`, ...). The reader allocates each type's indices in
definition order from the type's `first` index, so a name stands for whatever index the
playroutine needs.

- **Rows** use the shared row keys (see Shared vocabulary) wherever the playroutine's
  codec can express them: `{ctrl: 129, note: =68}`. A row no codec shape covers is
  written in the type's `form`: a `list` row holds the fields in declaration order, with
  trailing defaults left out (a GoatTracker wavetable portamento step `[241, 1]`); a `map`
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
  are defined in the first row: `vinit: {0: {...}, 1: {...}}`. They use the shared keys
  with their row meaning wherever one fits (`vinit` writes `ctrl`, `note`, `ad`, `sr`,
  `pw`, `af`, `ps`; `ginit` writes `res`, `route`, `mode`, `cp`), and an omitted field
  is 0.

## Events

A phrase is a list of event atoms:

```
note[.instrument][!command:arg[!command:arg]][*steps]
```

- `note` is a note name such as `C2`, `A#3` or `C10`, `-` for no note, `off` (gate off)
  or `on` (gate on). Names are real pitch: `C2` is 65.4 Hz in every song, whatever
  index the player's frequency table gives it (see `note_offset`).
- `.i3` names the instrument. For defMON it names a sidTAB program.
- `!run:@c1` is a named command with an argument: a number, `@name` for a command that
  references a record or an expression, or `none`. An event carries at most two commands,
  in the order the player reads them (for SID-Wizard: instrument column, note column, FX
  column). Command names are the ones below.
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
| `pw` | the 12-bit pulse width, a value or an expression |
| `cut` | the 11-bit cutoff over `$D415`/`$D416`, a value or an expression |
| `freq` | the voice frequency's expression; an integer is a constant offset from the note's frequency |
| `res` | the resonance, bits 4-7 of `$D417` (see Filter) |
| `route` | the filter routing, bits 0-3 of `$D417`: a mask, or an edit for the program's voice (see Filter) |
| `mode` | the `$D418` filter-mode bits 4-7, volume excluded (see Filter) |
| `run` | the program the row starts in the slot its kind implies, by name, as `!run:@x` |
| `vol` | the volume, the low nibble of `$D418`, as `!vol:n` |

| command | meaning |
|---|---|
| `!run:@x` | start program `x` in the slot its kind implies; `!run:none` stops it |
| `!run2:@x` | start program `x` in slot 2, for players with generic slots |
| `!ad:`, `!sr:`, `!ctrl:` | set the register |
| `!resfilt:n` | set `$D417` whole, `n = 16*res + route` |
| `!vol:n` | set the volume, the low nibble of `$D418` |
| `!cut:n` | set the 11-bit cutoff |
| `!tempo:n`, `!vtempo:n` | n ticks per step, for all voices / this voice |
| `!funk:@s` | swing tempo pair |
| `!freq:@x` | drive the voice frequency by expression `x` (see Modulators) |
| `!legato:0` | the event's note does not retrigger (see Legato) |
| `!vib:@s` | GoatTracker vibrato at the speed of `speedtab` row `s` |
| `!mark:n` | a timing mark that changes no sound |
| `!restart:@r` | the note's hard restart, for a player whose instruments have none (see Hard restart) |

SID-Wizard's effect commands are its own: they have no identical shared form.

| command | meaning |
|---|---|
| `!attack:n`, `!decay:n`, `!sustain:n`, `!release:n` | small FX $2x/$3x/$5x/$6x: one ADSR nibble |
| `!iattack`, `!idecay`, `!isustain`, `!irelease` | the same small FX in the instrument column, which the player times differently |

A shared key means the same wherever it appears: in program rows, in initial-state
records, and in instruments, where `ctrl` names the program run on the control register
(GoatTracker's `ctrl: [...]` or `ctrl: c1`) and `restart` the hard restart (see Hard restart). A row or command a player can't express is
rejected on reading, naming the key or command and the player (`goattracker2: the player
can't express !attack:1`); the writer refuses to write a value that would not read back
(a defMON pulse cell over `$0FFF`). Player-specific leftovers keep their v1 form:

- **GoatTracker 2:** the wavetable command rows with no shared form (list rows `[w, n]`, see
  the GoatTracker 2 backend), `!vib:@s` and the instrument keys `speed` and `vd`.
- **defMON:** the sidTAB column `cp`; `af` below $80 (a fixed offset).
- **SID-Wizard:** the instrument key `flags` (bits 6 and 7 of the header's control byte:
  keeping the pulse and filter programs across notes) and `octave`.

The instrument keys `gt`, `fw` (GoatTracker), `hrad`, `hrsr`, `firstctrl` and bits 0-3 of
`flags` (SID-Wizard) and defMON's `wgl` column are retired in v1.8: a document using them is
rejected, naming them (`ins.gt is retired in v1.8: write the hard restart as the
instrument's restart program`, `sidtab: wgl is retired in v1.8`). The list rows of
GoatTracker's pulse and filter tables and SID-Wizard's pulse, filter and WF tables are retired
in v1.10 (`pulsetab: row [32, 16] is retired in v1.10: a sweep, stall or keyboard-tracked row
is its program's expression`): such a row is part of its program's `pw` or `cut` expression,
which the lifter proves or fails on (see Lifting). A control mask is defMON's: GoatTracker and SID-Wizard reject it (`ctrl holds
no mask here`).
A vibrato is a `freq` expression in every player: the SID-Wizard instrument keys `vibrato`
and `vibdelay`, bits 4 and 5 of `flags` (the vibrato type) and the note-column command
`!notevib` are retired in v1.11 (`ins.vibrato is retired in v1.11: write the vibrato as the
instrument's freq expression`, `!notevib is retired in v1.11`). A field's retired bits are
written without, and read as its default's: `flags` reads with the plain vibrato type, which
no expression-driven note consults.
The format has no `mod` record type, no `!mod` or `!noteglide` command and no mod row
keys (`reg`, `step`, `rel`, `to`, `lo`, `hi`, `at`, `every`, `delay`, `for`); a document
using them is rejected (`record type 'mod' is retired in v1.5`, `!mod is retired in v1.5`,
`can't express !noteglide:0`). A command's argument is a byte: `!freq:@x` names one of the
first 255 expressions, else the reader refuses it.

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
| defMON | see below; `ginit` sets both, `route` as a mask | sidTAB rows and `ginit`, bits 4-7 | fixed at 15; `!vol` is rejected |

GoatTracker's `!resfilt:n` stays one command: the player sets both nibbles with one code,
and `!resfilt:0` also stops the filter program. SID-Wizard writes no routing in a row: it
routes the filter to every voice whose instrument has a filter program, so the routing
depends on what the other voices play and no row value can state it. A defMON sidTAB row
writes `$D417` in one of three ways: `{res: r, route: m}` with bit 3 of `m` clear sets the
byte; `{res: r, route: + m}` with bit 3 of `m` set keeps the routing and adds the voice and
`m`; `route: '-'` removes the voice and keeps the resonance.

`cut` is the 11-bit cutoff in every player. A defMON ACID program is written as its row's
`cut` expression, so the sidTAB column `acid` has no written form. The column `cp`, an
offset the replay adds to the ACID accumulator's high byte, stays: with it the cutoff
next to the model floor depends on the accumulator's low byte and carry, which a `cut`
expression over `v` cannot read.

The sidTAB columns `fv` (`mode`), `re` (`res` and `route`) and `acid` (`cut`) and the row key
`resfilt` (`res` and `route`) are refused (`fv is retired in v1.7`).
### Hard restart

A `restart` is a program (record type `restart`, prefix `r`) anchored to a note: its last
row applies on the note's first tick, and each earlier row its `hold` ticks before the row
after it. A row uses `hold`, `ctrl`, `gate`, `ad` and `sr`. GoatTracker and SID-Wizard
instruments name theirs (`restart: r1`); a defMON note names its own (`C3.t2!restart:@r1`):

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
the format writes it as the note's restart. On reading, a note's `!restart:@r` becomes
`!run2` of a sidTAB program of the lookahead rows (the last one frozen, holding until the
note), on the step their holds put before the note, which must be empty; the writer folds
every such `!run2` back: one alone on its step, followed only by empty steps up to a note
with a free command slot, running a program that ends, sets no column but `dl`, `wgh`,
`wgl`, `ad` and `sr`, and fits in the steps. The note's own sidTAB program starts on its
first tick, so the restart's last row is empty. A step is one tick in defMON, so `hold`
counts both.

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
| `pw` | the pulse width: 12 bits (GoatTracker), the 16-bit pair `pwhi:pwlo` (SID-Wizard), a 16-bit cell whose low 12 bits are the register (defMON) |
| `cut` | the 11-bit cutoff (GoatTracker, SID-Wizard); defMON's 17-bit ACID accumulator |

A unit with `scale: s` keeps its value in a cell that holds the chip value divided by
`s`: the player evaluates the expression with `v` as the cell times `s` and stores the
result floor-divided by `s`. GoatTracker's filter cell holds the top 8 bits of the cutoff
(`scale: 8`), so its expressions are in cutoff units like the other players'.

### Where expressions go

| place | players |
|---|---|
| a program row's `pw`, `cut` or `freq` | defMON sidTAB rows (`freq`, `pw`, `cut`); GoatTracker filter parameter rows and SID-Wizard filter set rows (`cut`) |
| an instrument's `pw` or `filter` program, written as the one expression | GoatTracker, SID-Wizard |
| an instrument's `freq`: the pitch expression a note starting the instrument runs | SID-Wizard |
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
| `command` | `freq` | `freq` | every tick of a step but tick 0, except ticks a wavetable row plays a note or runs a command | the step's command or argument changes, or a wavetable row plays a note | `!freq:@x` (command 16) |
| `pulse` | `pw` | `pulse` | each pulse-program tick: not on the tick the voice reads its next step, nor tick 0 of a step that starts a phrase | an instrument, `!run:@pw` or a wavetable `$F9` sets the program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `filtercutoff`, `scale: 8`, global | each filter-program tick | an instrument, `!run:@f`, `!resfilt:0` or a wavetable `$FA`/`$FB` sets the program | the program's first row (instrument `filter`) |

A `!freq` lasts its step. The same `!freq` on the next step continues the occurrence
(`φ` keeps counting); a different one, or a step with another or no command, starts the
next. Commands 1-4 and the instrument vibrato are the unit's own firings; a lifted song
writes their modulations as `!freq`.

### SID-Wizard

| unit | key | cell | fires | starts | written |
|---|---|---|---|---|---|
| `pitch` | `freq` | `voff` | every tick but the one a note starts on | a row writes `voff` or changes the vibrato or slide state, at the row or at its deferred FX | the instrument's `freq` for an instrument vibrato; else `!freq:@x` (FX 16) |
| `pulse` | `pw` | `pwhi:pwlo` | every tick but the one a note starts on | a note starts the pulse program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `vfhi:vflo:3`, mirrored to the global cutoff | every tick but the one a note starts on | a note starts the filter program | the program's first row (instrument `filter`) |

A `!freq` lasts until a note starts the instrument (which selects its `freq`) or another
pitch effect replaces it. Big FX $01-$03, note-column vibrato ($60-$77) and glide ($78)
are the pitch unit's own firings; a lifted song writes their modulations as `!freq`.
Speeds read the table `W[k]` (`exptab[k]` below 107, the frequency word of note index
`k - 107` above, size 204). The filter program runs for the voice that controls the
filter.

### defMON

| unit | key | cell | fires | starts | written |
|---|---|---|---|---|---|
| `pitch A` | `freq` | `sacc` | every tick, from layer A's row to the pitch step | a note, or layer A armed with a program that sets `af` or `freq` | the program's first row (its `af` becomes the slide mode $80) |
| `pulse A` | `pw` | `pw` | every tick, from layer A's row to the pulse step | layer A armed with a program that sets `pw`, `ps` or a pulse expression | the program's first row, replacing its `pw` and `ps` columns |
| `acid B` | `cut` | `acc`, global | every tick, from layer B's row to the next ACID step | layer B armed with a program that sets `acid` or a cutoff expression | the program's first row, replacing its `acid` column |

## Canonical form

The writer produces one text per song:

- names are given in first-use order;
- records and rows the song never reaches are dropped;
- identical phrases, and phrases that are equal up to a transpose, are merged, and
  the transpose moves into the play entry;
- equal expression texts share one definition;
- runs of empty steps become `*steps`;
- a playroutine's event restarts are written on their notes (see Hard restart);
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
| `prefix` | the name prefix |
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
  prefix: c
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
(`run: pw1`); the reader takes the shape whose `ref` is the type the name has. The writer
uses the first shape whose `when` holds and refuses a row that would not read back; a `map` row no
shape fits is an error. The reader accepts a written row only if encoding its inverse
gives the row again. A command entry has a `name`, an optional `ref` (a record type, or
`x` for an expression), and for a plain argument optional `when`, `arg` (raw to written)
and `raw` (written to raw), checked the same way.

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
| `scale` | the chip value per cell unit (default 1; see Units) |
| `scope` | `global` for one cell shared by the voices, owned by the voice whose start opened it |
| `fire` | the actions (or proc names) of one firing |
| `pre`, `post` | the functions that bound one firing (`post` defaults to `pre`): `v` is read before `pre` runs, the expression's value is stored after `post` |
| `x` | an expression over the voice's cells giving the expression a start selects; 0 leaves the playroutine's own firing |
| `hooks` | the starts: `{at: function, written: [cells], key: [cells], pointer: [cells]}`; the unit restarts when the function writes a `written` cell, changes the `key` cells, or writes a `pointer` to a program that sets one of `program.fields` |
| `program` | `{pointer, record, fields}`: the program a `pointer` selects and the fields that make it a start |
| `mirror` | a cell that takes the expression's value when it held the playroutine's |
| `label` | the cells that group a unit's occurrences for the lifter |
| `site` | where the lifter writes the expression: `{record, index, field, clear, first, when}` (`clear`: fields the expression replaces; `first`: fields set on the program's first row; `when`: over the start's cells, else the event's command) |
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

`unitracker.songio.loads` expands a document to the dict `SongPlayer` runs
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
native register grid exactly over that window; otherwise the lift fails. A start whose
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
gate-timer lookahead, the hold of the restart's first row. Instruments with one gate timer and
first wave share a restart program; a hard restart on gate timer 0 is not covered. Instrument 1 is pinned because every voice starts on it. A
filter parameter row carries the following cutoff row's value as `cut`, because the
player reads both in one tick. A wavetable delay row is `wait` on the row it delays. A
wavetable command row runs the pattern command of its low nibble, and the tick's continuous
effect does not run, as on a note row; the commands with a shared form are written with its key:
$F5 `ad`, $F6 `sr`, $F9 and $FA `run` (the pulse or filter program), $FB `res` and `route`
(but $FB 00, which also stops the filter program), $FC `cut` and $FD `vol` (below $10). A
wavetable jump is the program's `loop`. $F1-$F4 (one portamento, toneporta or vibrato
step at the row's speed), $F7 (a `ctrl` that, unlike a waveform row's, stops the tick's continuous effect),
$F0, $F8 and $FE (which the playroutine stops on), $F9 00 and $FA 00 (which `run: none`
can't tell apart), $FB 00 and $FD $10-$FF have no shared form.

**defMON.** The arranger and pattern counters are lifted as timing. Each song step
becomes one phrase per voice, all of the same length in ticks, with each event on the
tick the replay applies it. The playroutine fetches every tick and maps the event
fields as follows:

- `.t3` is the sidTAB program sidcall layer A starts;
- `!run2:@t5` arms layer B, and `!restart:@r1` arms it on the steps before the note;
- the note is a GATE_N note.

sidTAB rows are maps of the columns present, applied in the replay's bitmap-walk
order: `dl` is `hold` (a frozen row ends its program), `wgh` and `wgl` are `ctrl` and its
mask (`ctrl: 64^1`), `tr` is
`note`, the row's pitch, pulse and ACID modulators are `freq`, `pw` and `cut`, and the
other columns keep their names. Note names are offset by 24 indices: defMON index 48
is `C2`. The replay probes `$D41B` at init to tell the SID models apart; the result
becomes the playroutine constants `floor` and `dbl`. The ghost masks pulse-width high
to a nibble, as the reference grid does.

**SID-Wizard.** The playroutine runs each voice's tempo counter, reads a row at TICK_0,
applies it at TICK_2 and plays the next instrument's restart on the ticks between, ticking
the voices in the player's reverse order. An instrument is its header (`flags`, `ad`, `sr`,
`octave`), its WF/arp, pulse, filter and restart programs (`ctrl`, `pw`, `filter`,
`restart`) and its pitch expression (`freq`), which holds the header's vibrato; a
note-column vibrato is `!freq`. The header's hard-restart bytes and control bits 0-3 become
the restart program; a restart on both ticks before the
note (control bits 0 and 1) is not covered. WF rows use `ctrl`, `note`, `hold` and `freq`.
`hold` is the ticks the row lasts: on a row with `ctrl`, the instrument's arp speed plus one;
a row without `ctrl` keeps the waveform and lasts its native hold count plus one (1-16). A
program that ends paces itself afterwards by its last row's `hold`, so a hold row ending a
program at another arp speed is not covered. `freq` is the row's detune, a constant from -128
to 126 the player adds to the frequency word from that row until a later row sets another
(a row without it sets 0; a native row that keeps the detune is written with the one it
keeps, and the lifter refuses one that keeps different detunes on its loop's passes). Pulse
set rows use `pw`; filter set rows `mode`, `cut` and
`res`. A WF row with `a` $7F plays the chord: the lifter inlines the chord's offsets there,
each row with that row's waveform, which the player writes on every chord step. A chord
small FX ($7x) holds until the voice's next note, so on a note it becomes `!run` of the
instrument's program with that chord inlined. `!run` plays in the instrument column on an
event that names no instrument and in the FX column otherwise: the player times the two
differently on a note's hard-restart ticks. A chord FX on a row that starts no note, and an
FX-column chord on a note that names no instrument, are not covered. Note names are offset
by one index: SID-Wizard index 49 is `C4`. The lifter
requires each voice's first note to select an instrument: a note that selects none on a
voice that has never had one has no instrument state to play. Table jumps, orderlist
transposes, tempo and volume commands,
multispeed, other player builds and the effects not listed above are not covered yet;
the lifter names what it meets.
