# SID-Wizard (`sidwizard`)

The SID-Wizard 1.x player IRQ, normal build (driver 0), one play call per frame, of
[`sidwizard.yaml`](../../src/unitracker/playroutines/sidwizard.yaml).

## Lifting

| | |
|---|---|
| tune | a SID-Wizard 1.x export, normal build, decoded by pysidwizard |
| lifted from | the decoded module |
| tempo | the subtune's tempo pair: one tempo when both bytes have bit 7 set (the first byte), each byte 0 written as 6 |
| step | one pattern row; the playroutine runs each voice's tempo counter, reads a row at TICK_0, applies it at TICK_2 and plays the next instrument's restart on the ticks between, ticking the voices in the player's reverse order |
| notes | `note_offset` 1: SID-Wizard index 49 is `C4` |
| first note | each voice's first note must select an instrument |
| onset | the note's hard-restart tick, which sets its FREQ-HI: its programs sound on the next tick |
| before the first note | the first note's two set-up ticks read instrument 1's header as WF rows (the player's instrument 0 is linked to it, and its WF cursor starts at the control byte): the `idle` table holds the rows they read, ending in $FF, and sets the FREQ-LO the note's hard-restart tick carries; a jump or chord among them is not covered |

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

- A WF jump plays its target row on the same tick.
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
  pair from its next tick. A tempo of 0 plays as 6.
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
- A WF row with `a` $7F plays the chord: the lifter inlines the chord's offsets there, each row
  with that row's waveform, which the player writes on every chord step. Chords have no record
  type of their own.

## Filter

Filter set rows set `res` and `mode`; there is no routing in a row: the player routes the filter
to every voice whose instrument has a filter program, so the routing depends on what the other
voices play, and no row decodes a `route`. The volume is not covered.

## Hard restart

| lookahead row | first-tick row |
|---|---|
| at most one: `hold` 1 or 2, `gate: 0`, `ad`, `sr`, and `ctrl: 24` for a staccato restart (the voice's tables pause) | `{ctrl: n}` (the first waveform) or `{gate: 1}` |

A note sets the gate on itself, so the first-tick row always does, and a restart of that row alone
decodes as none.

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
  the instrument); a WF jump to a hold row, which the player plays as a waveform; a WF jump
  past $7F, which the player abandons, resting without the end's pitch carry;
- the orderlist's transpose, tempo and volume commands, multispeed, other player
  builds, init-routine voice-register writes, a tune with no instruments, and the effects not
  listed above (small FX $4x and $8x-$Fx, big FX but $01-$03, $10, $11, $13 and $14,
  note-column values past $78 but $7D and $7E).
