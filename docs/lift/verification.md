# Verifying a lift

A lift is exact when the document, played by unitracker-player, matches the format's spec player
(pysidtracker `SpecPlayer`) on two things each frame:

- the register grid: the 25 SID registers at the frame's end (`pysidtracker.gridcmp.cell_diff`);
- each voice's control edges within the frame (`unitracker.lift.shared.edges`).

The grid cannot show a gate cleared and set again within one play call, which restarts the attack,
or a TEST pulse that is set and cleared again, which resets the oscillator. Apart from these, a
frame's control, AD and SR writes come to the same envelope in any order unless the envelope's
rate counter matches between them (vagrant-moses `docs/sidorder.md`). That residual is player
timing noise, so the order of writes is not compared, only what persists:

| flag | where |
|---|---|
| 1 | the gate ends set, but was clear at the frame's start or at one of its writes |
| 2 | TEST was set at the frame's start or at one of its writes |

The reference side (`shared.spec_played`) takes the spec player's writes of each frame
(`SpecPlayer.writes`) and closes the frame with the grid's control registers, so writes the spec's
stores do not report (a ghost register block copied in, a `native` block) still reach the end
state. The document side (`shared.document_edges`) takes each voice's envelope segments
(`Player.segments`) late by the document's `lag`, and closes each frame with its own grid. A frame
whose flags differ is a cell of the voice's control register, `(frame, register, ours, theirs)`
with the flags as the values.
