# Music Assembler to the generic document, directly

`unitracker.direct.musicassembler` transcribes a Music Assembler SID to the generic document from
pymusicassembler's playroutine (`pymusicassembler.player.Player`, a byte-exact transcription of
the player run on the tune's own memory after its init). It reads every table and work cell
where the player does, at the addresses `pymusicassembler.layout` finds, and builds the document
with `unitracker_grammar.document`. It is a prototype: it covers the plain player and refuses the
rest (see Not transcribed).

```sh
python -m unitracker.direct.musicassembler TUNE.sid > tune.yaml
python -m unitracker.direct.musicassembler --verify [--frames N] [--diff K] [--jobs J] TUNE.sid ...
```

`--verify` plays each document with unitracker-player and compares its register grid with the
reference player's (`Player(pymusicassembler.read(raw))`, its native note table) over `--frames`
ticks. `musicassembler.standard(song)` is the reference with the document's note table: every
index the player reads its frequency table at sounds `F(k)`.

## Time

| Music Assembler | generic |
|---|---|
| a play call | a tick, `tempo:1` |
| the row tick (every `n + 1` calls, `n` the tempo reload) where a voice's duration runs out | the voice parses its next token and writes nothing: the last tick of its event |
| a token of duration `d` | an event of `(d + 1)(n + 1)` ticks, `*k`, from the call after its parse |
| the first call, which parses every voice | phrase `p0`, one tick of `-` |
| the orderlists' pattern starts (each repeat a start) | the timeline: a row where some voice starts a pattern, `~` elsewhere |
| orderlist loop | `{loop:k}`, the earliest row from which the timeline repeats |

## Tokens

| token | event |
|---|---|
| a note (`< $60`) and its control byte, after any instrument `$80-$9F` | the note at its index (transpose + note), `.iN`, `run:cK` |
| a note whose control byte has `$40` (the player's trigger latch, already set: no trigger) | the note with `legato`, no `.iN`: its first tick is a sustain row, not the trigger, so the instrument's programs, pulse and sweep run on |
| `$60-$7F`, or an instrument then a duration (the voice's instrument) | `off run:cK` |
| `$A0-$FE` | `- run:cK`: the gate is kept |

Every event runs its own control program `cK`, a row per tick it lasts, as the player's output
call writes them:

| tick | row |
|---|---|
| a note's first (the trigger) | `{ctrl:waveform}` |
| a later one, inner program running | `{ctrl:wave,note:N}`: the row's pitch, absolute or added to the note's index, `& $7F` |
| one with the program stopped and the instrument's `$10` flag, the first | `{freq:vib(w,m,s,k)}` |
| any other | none: the row before holds |
| the last (the next parse) | `{ad:AD}`, the AD the trigger wrote: nothing changes, and every slot holds on `row.ad` |

The note table is `F` at `clock_hz:985056`, the clock whose table is nearest the player's 96
words; no clock reproduces it (53 of notes 0-94 differ by one), so `--verify` reports the
frequency registers.

## Instruments

| Music Assembler | generic instrument |
|---|---|
| AD, SR | `ad`, `sr` |
| pulse byte `p`, flag `$40` (or a build that always sweeps) | `pw:pulse(p,s)`: `p` in both width bytes, then `+ 257 s` a tick |
| flag `$20` | `pw:pong(p,s,k)`: `- s` then `+ s` a tick, turning every `k = s & $0F` ticks |
| neither | `pw:257 p & $FFF` |
| every note resets the frequency to the table | `freq:none` |

## Code modulators

[`macode.py`](../../src/unitracker/direct/macode.py) transcribes the player's per-tick
functions; each slot holds on the voice's parse ticks (`hold:[row.ad]`).

| template | cell | state | routine |
|---|---|---|---|
| `pulse(p,s)`, `pong(p,s,k)` | pw | `d`, `q`: direction and count | the trigger's width, then `_pulse` |
| `vib(w,m,s,k)` | freq | `dl`, `sc`, `ph`: delay, count, phase | `_arp`: after `w = speed >> 3` calls, the word steps by `s` down or up as bit `phase & 3` of `m` (the player's 4-byte table), the phase turning every `k = speed & $0F` |
| `csweep(u,c,s)` | cut | `acc`, `ctr` | bound by `init` on the filter owner's voice, or by a note control `$80` (its two bytes `u` and `c`, `s = ((u & $0F) << 1) - $10`, `c` kept if 0; with `res`/`route` from the voice's preset or `$F0`) on that voice, reset on its notes (`reset:[note]`): the trigger's `acc = u`, `ctr = c`, then while `ctr` lasts `acc + s` to `$D416` |

## Not transcribed

Refused with a `TranscribeError`:

- a play routine other than the player's entry (a wrapper, interrupts), a voice dispatch other
  than voices 1-3 in order, tempo 0;
- an init that leaves the tempo or a duration running, writes a voice or the cutoff, or does
  not start the orderlists; a voice whose first token is no note while the image's control has the gate set (the image
  would sound);
- orderlist `$FC`/`$FD` commands, the silence pattern, a pattern without an end;
- a note control byte with the vibrato bit (`$20`), a legato note before its voice's first
  trigger; a filter command sweeping
  from more than one voice (the one sweep would need unbinding from the old owner);
- an instrument selected without a note that is not the voice's own;
- an inner program row with a filter byte, an inner program `$FD` jump;
- an arpeggio whose code reads other cells or compares on its descent.
