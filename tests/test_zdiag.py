"""Temporary diagnostic."""

import numpy as np
from unitracker_player.player import play

from unitracker.direct import musicassembler as m
from tests.test_direct_musicassembler import fixture_one, legato_notes  # noqa


def test_diag(one):
    before = m.Transcriber.document(one)
    legato_notes(one, 2)
    lines = []
    for v in range(3):
        starts = one._starts(v, 40)
        lines.append(f"v{v+1} starts {[(t, p) for t, p in starts][:3]}")
    text = one.document()
    lines.append(text[: text.index("[ins]") if "[ins]" in text else 600])
    i = text.index("p1") if "p1" in text else 0
    lines.append(text[-3000:])
    ours = play(text, ticks=16)
    ref = m.standard(one.song).render_grid(16)
    ref = np.asarray(ref, dtype=np.uint8)
    for v in (0, 2):
        for r in (0, 1, 2, 4):
            lines.append(f"v{v+1} reg{r} ours {ours[:16, v*7+r].tolist()} ref {ref[:16, v*7+r].tolist()}")
    lines.append("tokens v1: " + str([list(one._tokens(pid))[:4] for _, pid, _ in list(one._entries(0))[:1]]))
    assert False, "\n".join(lines)
