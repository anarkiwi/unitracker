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
| a note whose control byte has `$40` (the player's trigger latch, already set: no trigger) | the note with `legato`, no `.iN`: its first tick is a sustain row (with `note:` the new index, as the program rows' absolute note would hold the old one), not the trigger, so the instrument's programs, pulse and sweep run on; a running arpeggio is bound again as `freq:vibl(m,s,k)`, a slide as `freq:vslide(l,h)`, and a slide that stops `freq:none` |
| a note whose control byte has `$20` (two step bytes follow it, or the filter command's three where `$80` is set too) | the note with `freq:vslide(l,h)` in its trigger row; no arpeggio |
| `$60-$7F`, or an instrument then a duration | `off run:cK` |
| `$A0-$FE` | `- run:cK`: the gate is kept |

An instrument selected without a trigger (before a duration, or a legato note) changes only the
steps the voice's sweeps read from the next call: the trigger's modes, inner program, delay and
envelope run on. The event binds the running pulse sweep again at the new step, `pw:pstep(s)` or
`pw:pongs(s,k)`, and a running arpeggio at the new step and speed, `freq:vibs(m,s,k)`, none of
them restarting.

A voice whose first token is no note plays its image (the frequency, pulse width and control
init left) as commands of that event, which only sets the voice's instrument (`off.iN`): until
its first trigger the player's output keeps them.

Every event runs its own control program `cK`, a row per tick it lasts, as the player's output
call writes them:

| tick | row |
|---|---|
| a note's first (the trigger) | `{ctrl:waveform}`, with `freq:vslide(l,h)` for a note with `$20` |
| a later one, inner program running | `{ctrl:wave,note:N}`: the row's pitch, absolute or added to the note's index, `& $7F` |
| one with the program stopped and the instrument's `$10` flag, the first | `{freq:vib(w,m,s,k)}` |
| any other | none: the row before holds |
| the last (the next parse) | `{ad:AD}`, the AD the trigger wrote: nothing changes, and every slot holds on `row.ad`; with `res`/`route` where the next token has a filter command, whose `$D417` the parse writes (the first token's are `init`'s) |

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
| `pstep(s)`, `pongs(s,k)` | pw | `d`, `q` | `_pulse` at another instrument's step, from the width and state as they are |
| `vib(w,m,s,k)` | freq | `dl`, `sc`, `ph`: delay, count, phase | `_arp`: from `F(N)` (the player's working frequency), after `w = speed >> 3` calls, the word steps by `s` down or up as bit `phase & 3` of `m` (the player's 4-byte table), the phase turning every `k = speed & $0F` |
| `vibl(m,s,k)`, `vibs(m,s,k)` | freq | `dl`, `sc`, `ph` | `_arp` from a legato note: from `F(N)` at phase 0, the delay left; and at another instrument's step and speed, from the word and state as they are. A song that binds `vibl` or `vslide` resets its frequency slot on legato notes (`reset:[legato]`) |
| `vslide(l,h)` | freq | `wl`, `wh`: the word; `tg`: the toggle | `_sid_final` of a note control `$20` (its two bytes `l`, `h`, kept from the voice's last note where a filter command `$80` takes their place): the word starts at the event note's frequency and steps by `h << 8 \| l` a tick from the trigger tick, the ticks of an odd `l` alternating with ticks that sound `F(N)`, the working frequency; the note's arpeggio does not run |
| `csweep(u,c,s)` | cut | `acc`, `ctr` | bound by `init` on the filter owner's voice (from the accumulator and count init left), or by the trigger of the owner's first note since a note control `$80` (its two bytes `u` and `c`, `s = ((u & $0F) << 1) - $10`, `c` kept if 0; with `res`/`route` from the voice's preset or `$F0`) moved the sweep or changed it, reset on its notes (`reset:[note]`): the trigger's `acc = u`, `ctr = c`, then while `ctr` lasts `acc + s` to `$D416` |
| `idle()` | cut | | a filter command taking the sweep from a later voice: on its parse tick the old owner no longer steps it |

An inner program row with a filter byte writes it to the sweep's accumulator (`program_acc`, the
sweep's own cell) on its tick, a `post.cut:poke(u)!` row key: the `post` slot after the sweep's
fires `poke(u)`, `acc = u`, once, so the sweep steps on from `u` while its `ctr` lasts. The
player steps the sweep in its owner's output call, so the write is exact on the owner's voice and
the voices after it; a voice before the owner is refused.

## Not transcribed

A voice whose orderlist reaches the silence pattern `$FE` is parked there by the player, gate off,
its modulators running; it lifts as a gate-off event held to the end of the song, and the voice
does not count towards the song's loop.

Refused with a `TranscribeError`:

- a play routine that is not the player's entry, directly or through a chain of `JMP abs`
  (a wrapper that calls it, interrupts), a voice dispatch other
  than voices 1-3 in order, tempo 0;
- an init that leaves the tempo or a duration running, writes a voice or the cutoff, or does
  not start the orderlists; a voice whose first token is no note while the image's control has
  the gate set (the image would sound);
- the orderlist `$FC` tempo command, a pattern without an end;
- a legato note before its voice's first trigger, or with a filter command (its step would
  apply at once, its accumulator and count at the next trigger);
- an inner program filter byte on a voice before the sweep's owner;
- an arpeggio whose code reads other cells or compares on its descent.
