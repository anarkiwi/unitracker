# Write order

`tools/writeorder.py` finds the order in which a frame's SID register writes can be emitted while
keeping what tunes' players command. A canonical document carries an order of writes, not their
cycle timing, so the comparison abstracts timing away entirely.

## Event patterns

A voice's envelope reacts to its control, AD and SR writes in the order they arrive. A gate edge
switches between release and attack, and so which rate nibble is active; an AD or SR write changes
the active rate at once when its nibble is the one in effect (attack or decay after a gate-on,
release before it). The rate counter is not reset by either, so the order of gate, AD and SR writes
decides which rate the counter runs against when the attack starts and when it switches: a player
can lengthen or shorten the attack this way on purpose. A test-bit edge resets the oscillator.

So each frame's control, AD and SR writes to a voice, from the registers it starts with, reduce to
an **event pattern**: every write that changes the gate or test bit, the AD or the SR, in order.
Only writes of the value already held are dropped; nothing is collapsed as superseded. Frequency,
pulse width, filter and volume writes, and the cycles between writes, are not part of it: a
document carries the order of writes, not their timing. `a0A s88 g+t+` is AD `$0A`, SR `$88`, then a
gate-on with the test bit set; `g-t- g+t-` is a gate pulse within the frame.

## Orders

Every frame keeps each register's last write, and optionally the control write before the last
(`ctrl:2`, a gate or test pulse). Only the per-voice order of those kept writes (`ad`, `sr`,
`ctrl:2`, `ctrl`) can change a voice's pattern, which gives 18 orders. For each frame and voice the
tool compares the pattern each order gives against the native one, and counts:

- per order, the (frame, voice) cases it changes, per player;
- the distinct `native -> ordered` pattern pairs over the corpus, with their counts and the orders
  that give each.

The pattern pairs are few compared with the frames, since players repeat the same note-on and
hard-restart sequences; whether a given pair is audible is a question about that pair, answered
once rather than per frame.

## The rate counter

Whether an edge stalls the envelope (the ADSR delay) depends on the envelope's rate counter, a
15-bit LFSR whose phase no player sets. pylibresidfp's tests pin this: the test bit does not reach
the envelope, and after a hard restart (gate off, AD and SR zero for two frames) the counter is in
one of the nine states of the rate-0 cycle, where a gate-on with attack 0 stalls for 32772-32773
cycles at two of them (one with decay 0). The pattern records the envelope settings at each edge,
which decide that distribution; the phase itself is chance.

## Usage

```
python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--top K] [--out F.json]
```

Writes come from the in-process emulator (`--backend jennings`, tunes with a play address) or the
sidtrace oracle (`--backend sidtrace`); `auto` picks per tune.
