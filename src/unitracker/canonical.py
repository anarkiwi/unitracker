"""The canonical SID write order and the normalizer for native write streams."""

from collections.abc import Iterable, Iterator

REG_COUNT = 25

CANONICAL_ORDER = (0x15, 0x16, 0x17, 0x18) + tuple(
    7 * voice + off for voice in range(3) for off in (2, 3, 6, 5, 0, 1, 4)
)
_RANK = {reg: i for i, reg in enumerate(CANONICAL_ORDER)}


def canonical_frames(
    frames: Iterable[Iterable[tuple[int, int]]], regs=None
) -> Iterator[list[list[tuple[int, int]]]]:
    """Normalize per-frame ordered ``(reg, value)`` writes to canonical flushes.

    A frame is cut at each register written twice in its current segment; each
    segment keeps its changed registers in canonical order, and empty ones drop.
    """
    state = list(regs) if regs is not None else [None] * REG_COUNT
    for writes in frames:
        flushes = []
        segment = {}
        for reg, value in [*writes, (None, None)]:
            if reg is None or reg in segment:
                flush = sorted(
                    ((r, v) for r, v in segment.items() if state[r] != v),
                    key=lambda w: _RANK[w[0]],
                )
                for r, v in flush:
                    state[r] = v
                if flush:
                    flushes.append(flush)
                segment = {}
            if reg is not None:
                segment[reg] = value & 0xFF
        yield flushes
