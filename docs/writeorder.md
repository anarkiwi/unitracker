# Write order solver

`tools/writeorder.py` finds the SID register write order within a frame that best keeps what tunes'
players audibly command. It scores a fixed set of candidate orders against each tune's native
writes, frame by frame and voice by voice, on libresidfp (through pylibresidfp).

## Candidates

A candidate is an order of the frame's last write to every register, plus optionally the
second-last control write (`ctrl:2`). It is built from:

- each voice's gate and envelope writes (`ad`, `sr`, `ctrl`, and `ctrl:2` before `ctrl` when it
  is kept), in any order;
- those before or after the voice's frequency and pulse writes;
- the voices one after another (`voice(...)`) or interleaved register by register
  (`interleave(...)`);
- the song registers (`fc_lo`, `fc_hi`, `res_filt`, `mode_vol`) first or last.

That gives 144 candidates. A frame under a candidate keeps the writes the candidate names, in its
order, at the cycles the kept writes had (the frame's own slots), so order is judged apart from
the player's timing.

## What can be heard

Only three mechanisms in libresidfp make the order or timing of writes within a frame audible:

- **The envelope's rate counter.** Each voice's envelope steps on a 15-bit LFSR that runs
  every cycle and resets when it equals the active rate. A gate edge, or an AD/SR write that
  changes the active rate, may land when the LFSR has passed the new rate's state; the LFSR then
  runs its whole 32767-state sequence before the envelope steps (the ADSR delay). Envelope
  generators are independent of the oscillators and of each other, and only their own control,
  AD and SR writes reach them.
- **Sync and ring modulation**, which couple one voice's oscillator phase to another's.
- Everything else (frequency, pulse width, filter, volume) changes a parameter a few cycles
  earlier or later, which is inaudible.

Writes that are no-ops for an envelope are dropped before comparing: a control write that
keeps the gate bit, or an AD or SR write of the value the register holds.

## The rate counter's phase

The rate counter's phase is not something a player can set. pylibresidfp's tests pin this:

- the test bit does not reach the envelope at all (identical onsets with and without it);
- after a hard restart (gate off, AD and SR zero for two frames) the counter is in one of the
  nine states of the rate-0 cycle, and a gate-on with attack 0 stalls for 32772-32773 cycles at
  two of them (one with decay 0).

So a voice's commanded behaviour for a frame is the set of envelope outcomes over every phase
of its current rate's counter cycle, not the outcome at the phase the tune happened to be in.

## Verdicts

For each (frame, voice) whose envelope writes a candidate changes, both versions are run on the
voice's envelope state at the frame start through that frame and the next two (longer than any
ADSR delay), from every phase of the rate counter:

| verdict | meaning |
|---|---|
| `same` | the candidate leaves the voice's effective envelope writes as they were |
| `noise` | at every phase both take the same levels and states, every step within the cycles the writes moved |
| `drift` | the two sets of outcomes pair up across phases that way: the reorder changes only which phase stalls, which no player commands |
| `shape` | the levels or states differ (a retrigger lost, a rate in effect at a gate edge changed) |
| `timing` | a step moves further than the writes did (an ADSR delay begun or skipped for every phase alike) |

`shape` and `timing` are audible. The ranking is by audible (frame, voice) cases over all tunes,
with per-player counts.

## Usage

```
python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--top K] [--out F.json]
```

Tunes come from the in-process emulator (`--backend jennings`, tunes with a play address) or
the sidtrace oracle (`--backend sidtrace`); `auto` picks per tune.
