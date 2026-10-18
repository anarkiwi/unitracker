# defMON (`defmon`)

The defMON `$1000` replay of [`defmon.yaml`](../../src/unitracker/playroutines/defmon.yaml).


## Lifting

| | |
|---|---|
| tune | a defMON `$1000` replay `.sid`, run by pydefmon |
| lifted from | the replay's memory after init |
| step | one tick: the lifter simulates the arranger and pattern counters, so each song step becomes one phrase per voice, all of the same length in ticks, with each event on the tick the replay applies it; the playroutine fetches every tick |
| notes | `note_offset` 24: defMON index 48 is `C2`; a note is a GATE_N note |
| constants | `floor` and `dbl`, the result of the replay's `$D41B` probe at init, which tells the SID models apart: they are the SID model of the environment the tune is lifted in, not data of the tune |

The replay's init ($1522) clears `$D417`, `$D418`'s filter bits, the ACID accumulator, step and
offset `cp`, and each voice's envelope, control register and mask, pitch mode, pulse step,
sounding note and layer rows, and freezes both layers; the playroutine's init does the same, and
gives voice `k` the detune `k` and the routing bit `1 << k`. The ghost masks pulse-width high to
a nibble, as the reference grid does.

## Records

| record type | native table | notes |
|---|---|---|
| `ins` | none: the pair of sidTAB programs an event arms | see Events |
| `ctrl` | sidTAB | a sidTAB row writes any register, so it is defMON's one program table; see Control programs |
| `restart` | sidTAB programs on layer B before a note | see Hard restart |
| `vinit` | each voice's state after init (fixed) | `pw`, the pulse cell, at most $0FFF; `freq`, the frequency registers as the signed offset from `note`'s frequency (omitted: the note's frequency); `sacc`, the slide accumulator; `note` (`=n`), the note its relative rows add to |
| `ginit` | the global state after init (fixed) | `sub`: 1 when the ACID step subtracts (its SBC), 0 when it adds (its ADC), which the replay's own ACID firing reads until a layer-B row sets the step (setting the cutoff keeps it) |

The frequency registers in `vinit` are the voice's on the first tick only: init's call latches them,
then its pitch step sets the frequency from the note. The slide accumulator counts only under a
slide a layer-A program starts before the voice's first note, which clears it. There are no `pw` or
`filter` record types.

## Events

The playroutine declares [Instrument columns](../format/vocabulary.md#instrument-columns):
`instrument: {type: sidtab, base: 1, event: {type: ins, column: ctrl, commands: {1: filter},
restart: 1}}`.

- `.i3` names the instrument: its `ctrl` is the sidTAB program sidcall layer A starts (the
  event's instrument column), its `filter` the one layer B starts (the event's command), and a
  layer the instrument leaves out keeps running. The writer names one instrument per pair of
  programs the events arm (`{ctrl: c1, filter: c2}`, `{filter: c2}` on an event that arms only
  layer B), so `C2.i4` and `-.i5` are a note and an empty step arming them. An instrument's
  `restart` arms layer B on the steps before the note (see Hard restart), so the notes of one
  program with and without a restart name two instruments. An instrument holds no other key and
  arms a program or a restart.
- An event reads one command, so the slots are only an order.

## Commands

The player takes no shared command: each is refused by name (`defmon: the player can't express
!tempo:6`), and so is a document's `tempo`: timing is the timeline's, and the volume is fixed at
15.

## Control programs

| `ctrl` row key | forms |
|---|---|
| `hold` | value |
| `ctrl` | value, `^n` |
| `gate` | value |
| `note` | value |
| `ad` | value |
| `sr` | value |
| `pw` | expression |
| `cut` | expression |
| `res` | value |
| `route` | value, `+ m`, `-` |
| `mode` | value |
| `freq` | expression |

sidTAB rows are maps of the columns present, applied in the replay's bitmap-walk order: `dl` is
`hold` (a frozen row ends its program), `wgh` is `ctrl` (the waveform nibble, written with bit 0
set: the replay's gate is `wgl`'s bit 0), `wgl` is `gate` and the mask of its other bits
(`{ctrl: 81^4, gate: 1}` is `wgh` $50, `wgl` $05; a `wgl` row always writes `gate`, so the mask
`^n` holds until a row sets `gate` or another mask, and a row that sets `gate` without a mask sets
it to 0), `tr` is `note`, `fv` is `mode` (bits 4-7), `re` is `res` and `route`, and the
row's pitch, pulse and ACID modulators are `freq`, `pw` and `cut` (see Modulators). A step is one
tick, so `hold` counts both.

## Filter

A sidTAB row writes `$D417` in one of three ways: `{res: r, route: m}` with bit 3 of `m` clear
sets the byte (the byte 0 is `route: '-'`); `{res: r, route: + m}` with bit 3 of `m` set keeps
the routing and adds the voice and `m`; `route: '-'` removes the voice and keeps the resonance.

`cut` is the ACID accumulator: bits 8-15 are the cutoff byte, bits 0-7 its fraction and bit 16
the carry of the last step. The playroutine writes `$D416` as the cutoff byte plus the carry,
wrapped to 8 bits, raised to the model floor when it is below the floor or above $7F, and doubled
on the SID model that needs it. The replay's offset `cp`, which it adds to the accumulator's high
byte only on the way to `$D416`, is part of the accumulator: its high byte holds the offset,
wrapped to 8 bits as the replay's add-with-carry wraps it, so the cutoff byte is exact for every
offset. An ACID program, and a layer-B program that sets `cp`, is written as its row's `cut`
expression, its steps and saturation test carrying the offset as constants; `ginit` holds no
offset (the replay's init clears it and the accumulator). Layer B is the filter's: only a
layer-B row's `cut` is the ACID unit's.

## Hard restart

| lookahead row | first-tick row |
|---|---|
| one or more: `hold`, `ctrl` (a value, a mask `^n`, or both), `gate`, `ad`, `sr` | `{}` |

defMON has no instrument restart: composers run a short sidTAB program on layer B on the empty
steps before a note. The playroutine declares this with `event: {..., restart: 1}` (see
[Hard restart](../format/vocabulary.md#hard-restart)): the `restart` of a note's instrument reads
as a layer-B start (the event command an instrument's `filter` is) of a sidTAB program of the
lookahead rows, the last one frozen, holding until the
note, started as many steps before the note as they hold, all of them empty. The writer folds such a
start back before it names the events' instruments when it is alone on its step, followed only by
empty steps up to a note with a free command slot, and its program ends, sets no column but `dl`,
`wgh`, `wgl`, `ad` and `sr`, and fits in the steps. The note's own sidTAB program
starts on its first tick, so the restart's last row is empty.

## Lifetime

`!pw` and `!filter` are refused: an instrument's programs start on every event that names
it.

## Modulators

`F(k)` is `note_hi[k + 24] << 8 | note_lo[k + 24]`; `N` is the cell `note` less 24. `S[k]` is the
step of the portamento table at defMON index `k` (`por` word `k + 1` less word `k`, size 256), so
its index carries the 24.

| unit | key | cell | value `v` | fires | starts | written |
|---|---|---|---|---|---|---|
| `pitch A` | `freq` | `sacc` | the 16-bit offset added to the note's frequency: the slide accumulator, or with `af` below $80 the offset `S[N + 24 + af]` | every tick, from layer A's row to the pitch step | a note, or layer A armed with a program that sets `af` or `freq` | the program's first row, which runs the slide mode $80 (the program's other rows set no `af`) |
| `pulse A` | `pw` | `pw` | `pw & $FFF`: the 16-bit cell's top 4 bits are not part of `v`, and a result that differs from its low 12 bits is stored as the cell, clearing them | every tick, from layer A's row to the pulse step | layer A armed with a program that sets `pw`, `ps` or a pulse expression | the program's first row, replacing its `pw` and `ps` columns |
| `acid B` | `cut` | `acc`, global | the 17-bit accumulator, `cp` in its high byte (see Filter) | every tick, from layer B's row to the next ACID step | layer B armed with a program that sets `acid`, `cp` or a cutoff expression | the program's first row, replacing its `acid` and `cp` columns |

`af` below $80 adds a fixed offset to the note's frequency; the lifter writes it as the first
row's `freq` expression, `S[N + 36]` for `af` 12, which plays in the slide mode with the same
offset.

## Not covered

The lifter fails, naming what it meets:

- a tune that is not the `$1000` build (its code differs from the replay's at an address);
- a tune whose init leaves a cell the replay's init sets at another value, naming the cell, or
  gives a voice other detune or routing constants;
- ACID opcodes that are not a matching SBC/ADC pair, or a cutoff output opcode that is neither
  NOP nor ASL;
- a sidTAB row that resolves to a row with no body;
- an init that does not arm the arranger, a song step that never reaches an ALT event, or an
  arranger that does not loop;
- a modulation start whose expression is not proved (see [modfit.md](../modulation/modfit.md)).

Writing the lifted song fails, naming the row or record, on what has no written form: a `cp` on a
layer-A row, and a voice's initial pulse cell over $0FFF.

A program that changes the pitch mode after its first row, or sets `af` below $80 under
relative-note rows, is not covered: the slide mode drops the voice's detune before the change, and
the offset of a relative note reads the note base.
