# SID-Wizard (`sidwizard`)

The SID-Wizard 1.x player IRQ, every build (drivers 0-5) at any number of play calls per
frame, of [`sidwizard.yaml`](../../src/unitracker/playroutines/sidwizard.yaml).

## Lifting

| | |
|---|---|
| tune | a SID-Wizard 1.x export, decoded by pysidwizard |
| build | the export's player build (normal, medium, light, extra, bare, demo; see Builds) |
| tick | one play call: `frame_speed` per frame (see Multispeed) |
| lifted from | the decoded module |
| tempo | the subtune's tempo pair: one tempo when the first byte has bit 7 set, each byte 0 written as 6 |
| step | one pattern row; the playroutine runs each voice's tempo counter, reads a row at TICK_0, applies it at TICK_2 and plays the next instrument's restart on the ticks between, ticking the voices in the player's reverse order |
| notes | `note_offset` 1: SID-Wizard index 49 is `C4` |
| first note | each voice's first note must select an instrument |
| onset | the note's hard-restart tick, which sets its FREQ-HI: its programs sound on the next tick |
| build | the `build` table: 1 for a pre-1.8 driver, whose hard-restart tick reads FREQ-HI from FREQTBH at the voice's track offset (7 times the voice) instead of at the note |
| before the first note | the first note's two set-up ticks read the header of the player's instrument 0 as WF rows (linked to instrument 1 unless the image links another; its WF cursor starts at the control byte): the `idle` table holds the rows they read, ending in $FF, and sets the FREQ-LO the note's hard-restart tick carries; a jump past $7F rests there, and another jump or a chord among them is not covered |

## Builds

The builds are one `player.asm` assembled with different `feature` flags, which pysidwizard
reads from the export's player code (`SWMFile.features`), else gives by its driver byte
(`pysidwizard.features.features_for_driver`). The lifter reads the tune's build there and
expresses it two ways:

- as data, where a flag only stops the player reading a field: without octave shifts every
  instrument's shift is 0; without the pulse or filter reset switch the header's control bits 6
  and 7 are clear; without pulse or filter keyboard tracking (which also reads the filter
  routing) each row's third byte is 0; without detune the WF rows set none; without arp speeds
  every WF row lasts one tick and a WF byte below $10 is a waveform; without hard-restart types
  every restart fires from TICK_0 with the default envelope ($0F/$00), never staccato; without
  the first-frame switch the first tick always writes control $09;
- as the playroutine's `constants`, each named as its `PlayerFeatures` field, where a flag
  changes what a proc does: `arpspeedsupp` (hold rows), `calcvibrato` (the vibrato and slide
  amplitude: without it the raw amplitude indexes FREQTBH-1), `detunesupport` (the detune and
  the carry into FREQ-LO), `finefiltsweep` (without it a sweep steps the cutoff's high
  byte), `frame1switch` (the first tick's FREQ-HI), `allghostregs` (an
  envelope small FX keeps the live other nibble), `vibslidealways` (the tables walk on a new
  note's set-up ticks), `wfarp_nop_supp` (without it a WF arp of $80 is absolute note 0),
  `chordsupport` (without it a WF arp of $7F is a relative note), `portavibra` (a portamento
  tests its target one unit nearer and returns to the instrument's vibrato there; a note-FX
  portamento ends at once), `vibratotypes` (vibrato type $00 grows its amplitude by the delay
  byte, and types $20 and $30 start the vibrato counter at half and none; without it every
  type is the plain vibrato) and `multispeedsupp` (without it the multispeed calls step a
  note's programs from the note). The YAML holds the normal build's values.

A build that walks the tables on a note's set-up ticks steps the pulse program through
instrument 1's header before each voice's first note: the lifter reads the pulse word and
sweep count each voice's first note starts with from the native player, into `vinit` (`pw`,
written as the init row's `!pw`) and the `pwcount` table.

## Multispeed

A tune exported at `frame_speed` calls per frame runs the player on one call of each frame and
MULPLY on the others, which steps each voice's WF program, its pulse program when the
instrument's arp speed has bit 6 or 7 set and its filter program when it has bit 7 (`ins.mul`),
from the voice's first player tick after its note. The first call is a MULPLY call. A tick is one call: the clock's
`cycles_per_tick` is the frame's divided by `frame_speed`, the constant `speed` is
`frame_speed`, and every count of player ticks is written in calls: the tempo, the tempo FX and
a restart's lookahead `hold`, and its first-tick row lasts `speed` ticks. Before its first note
a voice walks instrument 1's WF rows on every MULPLY call, following its jumps: `idle` holds
every row it reads.

## Records

| record type | native table | notes |
|---|---|---|
| `ins` | instrument header | keys `ad`, `sr`, `ctrl`, `pw`, `filter`, `freq`, `restart`; the header's vibrato is its `freq` expression, its hard-restart bytes and control bits 0-3 its `restart`, control bits 6 and 7 its notes' `!pw` and `!filter` (see Lifetime), and its octave shift is in the notes it plays, each written as the pitch it sounds |
| `ctrl` | WF/arp table (`wftab`) | see Control programs |
| `pw` | pulse table (`pwtab`) | set rows use `pw`; a program with a sweep or keyboard-tracked row is its `pw` expression |
| `filter` | filter table (`flttab`) | set rows use `mode` (bits 4-6), `cut` and `res`; a program with a sweep or keyboard-tracked row is its `cut` expression; no row decodes a `route` |
| `restart` | the header's hard-restart bytes | see Hard restart |

A note is read as the player's note with no octave shift, which plays the same pitch.

## Table jumps

A table row whose first byte is $FE jumps: its second byte is an offset in the instrument's
memory (header, WF table, its $FF, pulse table, its $FF, filter table), which the player reads
from wherever it lands. The lifter follows each program from its entry through its jumps, so a
program is the rows it plays in order: a jump back to a row it has played is its loop (`next` to
that row), a jump forward or into another table's bytes continues there, and a jump to its own
row rests, as the end does.

- A WF jump plays its target row on the same tick; one past $7F the player abandons, and the
  program rests there as at its end.
- A pulse or filter jump to a set row plays it on the same tick; one to a sweep row only moves
  there, which spends a tick: the lifter writes a sweep row of no ticks, carrying the keyboard
  track of the row before the jump.

## Events

`.i3` names the instrument. The first slot is the instrument column and the second the FX
column. `!ad` and `!sr` play in the column of their slot (see Commands); `!freq`, `!legato`,
`!run` and the timing commands play in their own. The FX column holds one command; an event's
other command is an instrument-column form (`!ad` or `!sr` in the first slot of an event that
names no instrument, `!run` on such an event) or `!legato`.

## Commands

| command | argument |
|---|---|
| `!run` | `ctrl` name, `none` |
| `!pw` | no argument |
| `!filter` | no argument |
| `!ad` | 0-255, at most one nibble from the instrument's |
| `!sr` | 0-255, at most one nibble from the instrument's |
| `!tempo` | 3-127, 3-15/3-15 (two different) |
| `!vtempo` | 3-127, 3-15/3-15 (two different) |
| `!freq` | `x` name |
| `!legato` | no argument |


- `!run` plays a chord: it is a chord small FX ($7x), which holds until the voice's next note, so
  on a note it is `!run` of the instrument's program with that chord inlined. It plays in the
  instrument column on an event that names no instrument and in the FX column otherwise: the
  player times the two differently on a note's hard-restart ticks.
- `!ad` and `!sr` are the envelope small FX ($2x attack, $3x decay, $5x sustain, $6x release;
  playroutine codes $12x-$16x from the instrument column), each setting one nibble's override, which
  clears the other nibble's of that register and lasts until the voice's next note start (see
  [Envelope commands](schema.md#envelope-commands)). Each decodes as the byte it makes of
  the instrument's. An FX-column one alone on a row that starts no note decodes into the first
  slot, which plays the same.
- The tempo FX apply on the row's TICK_2 with its other FX: $10 (`!tempo:n`, playroutine code
  $12, as $10 is `!freq`) sets every voice's tempo pair to its value, $13 (`!vtempo:n`) this
  voice's, $11 (`!tempo:a/b`) every voice's to its nibbles and $14 (`!vtempo:a/b`) this voice's.
  Each rewinds the swing to its first tempo, and each voice counts its current row against the new
  pair from its next tick. A tempo of 0 plays as 6. A row ends when its count reaches a single
  tempo or a swing pair's second, but only exactly at a swing pair's first: a voice already past
  that plays the row for 128 ticks and keeps the first tempo.
- `!legato` is instrument column $3F.
- `!freq` is playroutine code $10, which no native FX has: it runs the expression it names.

## Control programs

| `ctrl` row key | forms |
|---|---|
| `hold` | value |
| `ctrl` | value |
| `note` | value |
| `freq` | value |

- `hold` is the ticks the row lasts: on a row with `ctrl`, the instrument's arp speed plus one;
  a row without `ctrl` keeps the waveform and lasts its native hold count plus one (1-16). A
  program that ends paces itself afterwards by its last row's `hold`.
- `freq` is the row's detune byte, written signed (-128 to 127 but -1, the byte that keeps the
  detune), which the player adds unsigned to the frequency word from that row until a later row
  sets another; a row without it sets 0, and a native row that
  keeps the detune is written with the one it keeps.
- A note keeps the frequency the voice held until its program's first row with a `note`.
- A WF row with `a` $7F plays the chord: the lifter inlines the chord's offsets there, each row
  with that row's waveform, which the player writes on every chord step. Chords have no record
  type of their own.

## Filter

Filter set rows set `res` and `mode`; there is no routing in a row: the player routes the filter
to every voice whose instrument has a filter program, so the routing depends on what the other
voices play, and no row decodes a `route`. The volume is not covered.

A note that restarts the filter program routes the voice; unless the program starts with a sweep
of none ($00) it also takes the filter and its one cursor. The player keeps that cursor's offset
in instrument memory, so the voice controlling the filter that starts a program beginning $00
walks the new instrument's bytes from the old program's place, which the lifted rows do not
state: such a song does not verify.

## Hard restart

| lookahead row | first-tick row |
|---|---|
| at most one: `hold` 1 or 2, `gate: 0`, `ad`, `sr`, and `ctrl: 24` for a staccato restart (the voice's tables pause) | `{ctrl: n}` (the first waveform) or `{gate: 1}` |

A note sets the gate on itself, so the first-tick row always does, and a restart of that row alone
decodes as none. A starting note's envelope small FX apply on its first tick after its
instrument's envelope, as its `!ad` and `!sr` play with the first-tick row
([Hard restart](https://github.com/anarkiwi/unitracker-player/blob/main/docs/semantics.md#hard-restart)).

## Lifetime

`!pw` and `!filter` are the header's control bits 6 and 7 (`flag` `{flags: 0x40}` and
`{flags: 0x80}`), which keep the program running on a note that selects no instrument. A note
that names an instrument restarts both programs. The lift fails on a phrase note played under
instruments of which one keeps a program and another restarts it.

## Modulators

`N` is `note + oct - 1`: the sounding note, less `note_offset`. Speeds read the
table `W[k]` (`exptab[k]` below 107, the frequency word of note index `k - 107` above, size
204); keyboard tracking reads `E[i]`, the player's EXPTABH (0 below 11, then the frequency high
bytes from index `i - 11`, size 256).

| unit | key | cell | value `v` | fires | starts | written |
|---|---|---|---|---|---|---|
| `pitch` | `freq` | `voff` | the offset added to the note's frequency | every tick but the one a note starts on | a row writes `voff` or changes the vibrato or slide state, at the row or at its deferred FX | the instrument's `freq` for an instrument vibrato; else `!freq:x` |
| `pulse` | `pw` | `pwhi:pwlo` | the 12-bit register: the cell plus the row's keyboard-track term | every tick but the one a note starts on | a note starts the pulse program | the program's first row (instrument `pw`) |
| `filter` | `cut` | `vfhi:vflo:3`, mirrored to the global cutoff | the 11-bit cutoff: the cell plus the row's keyboard-track term | every tick the voice controls the filter but the one a note starts on | a note starts the filter program | the program's first row (instrument `filter`) |

- A `!freq` lasts until a note starts the instrument (which selects its `freq`) or another pitch
  effect replaces it. Big FX $01-$03, note-column vibrato ($60-$77) and glide ($78) are the pitch
  unit's own firings; the lifter writes their modulations as `!freq`.
- A pulse row's nonzero keyboard-track amount `k` (its third byte) adds a term of the note to the
  register's high nibble from the row until a later row's amount replaces it: with `y` the sum
  `N + 1 + k` wrapped to a byte and `c` its carry, `256·(E[y] - E[y - 1] + c - [E[y] - E[y - 1] +
  c < 1])`. The program's `pw` expression carries it as a term of `N`.
- The filter program runs for the voice that controls the filter, and a filter row's nonzero
  amount adds `E[y] + c` to the cutoff's high byte, with `y` and `c` from the controlling voice's
  note, from the row until a later row's amount replaces it; the program's `cut` expression
  carries it as a term of `N`.

## Not covered

The lifter fails, naming what it meets:

- a row that selects an instrument of another octave shift without a note, and a phrase note
  played under instruments of different shifts;
- a note whose shift sounds below C0;
- a restart on both ticks before the note (control bits 0 and 1);
- an instrument marked unfiltered (filter table $FF);
- a hold row ending a program at another arp speed; a native WF row that keeps different detunes
  on its loop's passes;
- a $7F WF row whose chord the chord table lacks, a chord offset of $80 or more, and a chord that
  ends ($7E) returning to or from a WF hold row;
- a chord FX on a row that starts no note, an FX-column chord on a note that names no
  instrument, and a chord FX whose note plays under different instruments;
- a row with three effects;
- an envelope small FX on a voice with no instrument, one that writes the other register with
  another byte (after a row that selects an instrument without a note), an FX-column one alone
  on a note that names no instrument, and a play of a phrase that differs on its loop's passes;
- a tempo of 1 or 2, which never applies a row, in a tempo FX or the subtune's tempo; the lifter
  names the pattern and row of each row it refuses;
- a keyboard-tracked cutoff past $FF, whose carry into `$D416` no cutoff value states; a filter
  third byte of $80-$8F, which sets the routing;
- a voice's first note that selects no instrument;
- a table that starts with a jump; a jump that lands on no row (a jump, an end marker or past
  the instrument); a WF jump to a hold row, which the player plays as a waveform;
- the orderlist's transpose, tempo and volume commands, init-routine voice-register writes, a tune with no instruments, and the effects not
  listed above (small FX $4x and $8x-$Fx, big FX but $01-$03, $10, $11, $13 and $14,
  note-column values past $78 but $7D and $7E).
