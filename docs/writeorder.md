# Write order and hard restart

A canonical document carries the order of a frame's SID register writes, not their cycle timing.
These tools find which orders, and which hard restart, keep what tunes' players command, with the
SID's envelope (libresidfp through pylibresidfp) as the judge:

- `tools/writeorder.py`: each frame's event pattern per voice, and how each per-voice order of
  the kept writes changes it (fast, no emulation);
- `tools/ordercover.py`: the fewest orders that keep every frame's envelope exactly, and how
  tunes' hard restarts behave;
- `tools/orderaudio.py`: one order's differences measured and rendered at a fixed pitch;
- `tools/samecall.py`: registers a player writes more than once in one call.

## What reaches the envelope

A voice's envelope reacts to its control, AD and SR writes in the order they arrive:

- a gate edge switches between release and attack, and so which rate nibble is in use;
- an AD write changes the rate at once with the gate on (attack or decay) and is latent with the
  gate off (it takes effect at the next gate-on);
- an SR write changes the release rate at once with the gate off, and the sustain level with it
  on;
- the rate counter, a 15-bit LFSR, is reset by neither, so the order of gate, AD and SR writes
  decides which rate it runs against when the attack starts or switches. A player can lengthen or
  shorten the attack this way.

The test bit does not reach the envelope. The gate reaches the envelope's state within 2-3
cycles, less than one 6502 store, so a player's next write always meets the new state.

## Event patterns

`writeorder.py` reduces each frame's control, AD and SR writes to a voice, from the registers it
starts with, to its **event pattern**: every write that changes the gate or test bit, the AD or the
SR, in order; with the gate off an AD write is placed just before the next gate-on (or at the
frame's end), its last value only. Writes of the value already held and timing leave no trace.
`a0A s88 g+t+` is AD `$0A`, SR `$88`, then a gate-on with the test bit set.

## Orders

Every frame keeps each register's last write, and optionally the write before the last of the
control (`ctrl:2`, a gate or test pulse) and of SR (`sr:2`). A per-voice order arranges the kept
`ad`, `sr`, `ctrl` (and `ctrl:2`, `sr:2`, each before its last write) in the frame's own write
slots: 60 orders.

`ordercover.py` plays each (frame, voice) with an envelope event at a fixed pitch from the voice's
real envelope state, under every order, over the frame and the following ones, and keeps the
orders whose envelope trace is identical to the native one. The exact minimum cover of those sets
is the set of canonical orders: on the corpora measured so far, three, each needed by different
player builds:

| order | for |
|---|---|
| `sr:2,sr,ad,ctrl:2,ctrl` | most players: the envelope before the gate, SR first |
| `ad,sr:2,sr,ctrl:2,ctrl` | builds writing AD before SR with the gate on |
| `ctrl:2,ctrl,ad,sr:2,sr` | builds writing the gate before the envelope (ascending register dumps) |

Every case measured is kept exactly by one of the three, and nearly every tune needs only one of
them for all its frames.

## Hard restart

A hard restart turns the gate off with SR's release nibble 0 some frames before a note. Once it
has lasted a full rate-counter wrap (32767 cycles, during which the envelope does not step) and the
release from the highest level to 0 at rate 0, the voice is in the same state whatever came before:
the envelope at 0 and the rate counter in rate 0's nine-state cycle. A PAL frame is 9 x 2184
cycles, so longer hard restarts change nothing more. `ordercover.py` measures that bound on the
chip (`settle_cycles`) and classes each note-on by the whole frames of hard restart before its
frame.

Most notes in tunes have a shorter hard restart (or none), so their attack depends on the note
before; what a tune commands is the hard restart's length. The canonical hard restart is therefore
one parameter, its length k in frames (0, 1, 2, or settled). The AD and SR values written during it
do not matter (checked per note: the same writes with AD 0 and SR's sustain 0 give an identical
envelope), except in builds that write the gate before the envelope, where the gate-on uses the
AD and SR the hard restart left.

## Usage

```
python tools/writeorder.py TUNES.csv|TUNE.sid ... [--frames N] [--top K] [--out F.json]
python tools/ordercover.py TUNES.csv|TUNE.sid ... [--frames N] [--horizon N] [--out F.json]
python tools/orderaudio.py TUNES.csv|TUNE.sid ... [--order ORDER] [--wav DIR]
```

Writes come from the in-process emulator (`--backend jennings`, tunes with a play address) or the
sidtrace oracle (`--backend sidtrace`); `auto` picks per tune.
