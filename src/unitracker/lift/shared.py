"""What the format lifts share: their errors, definition names, the check against a reference
grid and its gate edges, note names, quoted meta values, bare rest runs and repeat detection."""

import multiprocessing
import re
import warnings

import numpy as np
from pysidtracker import gridcmp
from unitracker_grammar.document import Call, Event
from unitracker_player.player import Player, load, play

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
REST = Event("-")
_PLAIN = re.compile(r"[^\s,\[\]{}'\"#&*!|>%@`:?-][^,\[\]{}]*")
_YAML_WORDS = re.compile(r"(?i:y|n|yes|no|true|false|on|off|null|~)|[-+]?\d[\d_.:eE+-]*")


def note_name(k: int) -> str:
    """Generic note ``k`` (12 * octave + pitch class) by name."""
    return f"{NAMES[k % 12]}{k // 12}"


def meta_text(value: str) -> str:
    """A meta value as the document writes it: plain where YAML reads it back as that text,
    else single-quoted."""
    plain = _PLAIN.fullmatch(value) and not _YAML_WORDS.fullmatch(value)
    if plain and ": " not in value and " #" not in value and value == value.strip():
        return value
    return "'" + value.replace("'", "''") + "'"


def rests(events: list) -> list:
    """Consecutive bare rests as one ``-*k``."""
    out = []
    for event in events:
        last = out[-1] if out else None
        if event == REST and last is not None and Event("-", steps=last.steps) == last:
            out[-1] = Event("-", steps=(last.steps or 1) + 1)
        else:
            out.append(event)
    return out


def repeat(rows: list, gaps: list):
    """``(k, period)``: the earliest row from which the rows and their lengths repeat with the
    shortest period, seen at least twice; None where they do not."""
    keyed = list(zip(rows, gaps))
    n = len(keyed)
    for k in range(n):
        for period in range(1, (n - k) // 2 + 1):
            if all(keyed[i] == keyed[i + period] for i in range(k, n - period)):
                return k, period
    return None


class LiftError(ValueError):
    """A tune the lift cannot state exactly, or whose document differs from the reference."""


class Refusal(LiftError):
    """A construct of a tune the document cannot state; ``kind`` names its class."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Divergence(LiftError):
    """The document's grid differs from the reference player's."""


class Names:
    """Definitions by kind, numbered in first-use order, each with the origin it was first named
    for."""

    def __init__(self):
        self.kinds, self.origins = {}, {}

    def name(self, kind: str, prefix: str, value, origin=None) -> str:
        """The name of ``value`` among the ``kind`` records, adding it with ``origin``."""
        store = self.kinds.setdefault(kind, {})
        for name, held in store.items():
            if held == value:
                return name
        store[name := f"{prefix}{len(store) + 1}"] = value
        self.origins.setdefault(kind, {})[name] = origin
        return name

    def section(self, kind: str) -> list:
        """The records of ``kind``."""
        return list(self.kinds.get(kind, {}).items())


def calls_of(node):
    """The names of the calls under ``node``, in its printed order."""
    if isinstance(node, Call):
        yield node.name
    elif isinstance(node, (tuple, list)):
        for item in node:
            yield from calls_of(item)
    elif hasattr(node, "__dataclass_fields__"):
        for name in node.__dataclass_fields__:
            yield from calls_of(getattr(node, name))


CONTROLS = np.array([4, 11, 18])


def edges(frame, reg, value, frames: int) -> np.ndarray:
    """``(frames, 3)`` of what each voice's control writes, ``(frame, register, value)`` in write
    order from power-on with every frame's last write its end state, do in a frame beyond that
    end state: 1 where the gate ends set but was clear at the frame's start or a write (the
    envelope attacks again), 2 where TEST was set at either (the oscillator is reset)."""
    keep = np.isin(reg, CONTROLS)
    f, v, s = np.asarray(frame)[keep], (np.asarray(reg)[keep] - 4) // 7, np.asarray(value)[keep]
    order = np.lexsort((np.arange(len(f)), f, v))
    f, v, s = f[order], v[order], s[order].astype(np.uint8)
    out = np.zeros((frames, 3), np.uint8)
    if not f.size:
        return out
    key = v * frames + f
    first = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    start = np.r_[np.uint8(0), s[first[1:] - 1]]
    start[np.r_[True, v[first[1:]] != v[first[:-1]]]] = 0
    clear = (np.minimum.reduceat(s & 1, first) & start & 1) ^ 1
    test = (np.maximum.reduceat(s & 8, first) | start & 8) >> 2
    out[f[first], v[first]] = clear & s[np.r_[first[1:], len(s)] - 1] | test
    return out


def spec_played(player, frames: int) -> tuple:
    """``(grid, edges)`` of a spec player over ``frames`` frames (:func:`edges`), each frame's
    writes closed by its control registers as the grid has them, so writes the spec's stores do
    not make (a ghost copied in, a ``native`` block) still reach the frame's end state."""
    grid, writes = np.zeros((frames, 25), np.uint8), []
    for t in range(frames):
        grid[t] = player.render_grid(1)[0]
        writes += [(t, r, v) for r, v in player.writes]
        writes += [(t, r, grid[t, r]) for r in CONTROLS]
    w = np.asarray(writes, np.int64).reshape(-1, 3)
    return grid, edges(w[:, 0], w[:, 1], w[:, 2], frames)


def document_edges(song, grid: np.ndarray) -> np.ndarray:
    """:func:`edges` of a document's Song over the frames of its ``grid``, its writes as late as
    its registers (``lag`` calls of whole frames), each frame closed by its ``grid``."""
    if song.lag % song.frame:
        raise Refusal("lag", "a lag of part of a frame")
    player, rows, late = Player(song), [], song.lag // song.frame
    for t in range(len(grid)):
        player.step()
        rows += [
            (t + late, 4 + 7 * v, s[2]) for v, segs in enumerate(player.segments) for s in segs
        ]
        rows += [(t, r, grid[t, r]) for r in CONTROLS]
    w = np.asarray(rows, np.int64).reshape(-1, 3)
    w = w[w[:, 0] < len(grid)]
    return edges(w[:, 0], w[:, 1], w[:, 2], len(grid))


def edge_cells(ours: np.ndarray, theirs: np.ndarray) -> np.ndarray:
    """``(frame, control register, ours, theirs)`` where two :func:`edges` codes differ."""
    n = min(len(ours), len(theirs))
    f, v = np.nonzero(ours[:n] != theirs[:n])
    return np.c_[f, CONTROLS[v], ours[f, v].astype(np.int64), theirs[f, v].astype(np.int64)]


_JOB = []


def _run_job(frames: int):
    """The registered reference over ``frames`` frames, run in a forked worker."""
    return _JOB[0](frames)


class Reference:
    """The grid, or ``(grid, edges)``, of ``reference(frames)`` played in a forked worker from
    construction on, so it runs alongside whatever the lifter does before it needs the grid."""

    def __init__(self, reference, frames: int):
        _JOB[:] = [reference]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._pool = multiprocessing.get_context("fork").Pool(1)
        _JOB.clear()
        self._pending = self._pool.apply_async(_run_job, (frames,))

    def grid(self):
        """The reference grid or ``(grid, edges)``, waiting for the worker; the worker ends."""
        try:
            return self._pending.get()
        finally:
            self.close()

    def close(self):
        """Ends the worker, whether or not its grid was taken."""
        self._pool.terminate()


def cells_of(text: str, frames: int, grid=None, reference=None) -> np.ndarray:
    """The cells where the document ``text`` played over ``frames`` frames differs from ``grid``,
    or from ``reference(n)`` over the ``n`` frames it played, or from the grid of a started
    :class:`Reference`. A looping document plays exactly ``frames`` frames, so its reference is
    played in a forked worker alongside it. A reference of ``(grid, edges)`` also has each
    voice-frame whose gate and TEST edges differ (:func:`edge_cells`) as a cell of its control
    register."""
    song = load(text)
    job = reference if isinstance(reference, Reference) else None
    if job is None and grid is None and song.looping:
        job = Reference(reference, frames)
    try:
        ours = np.asarray(play(song, frames), dtype=np.uint8)
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is not None:
        grid = job.grid()
    if grid is None:
        grid = reference(len(ours))
    grid, theirs = grid if isinstance(grid, tuple) else (grid, None)
    cells = gridcmp.cell_diff(ours, np.asarray(grid, dtype=np.uint8)[: len(ours)])
    if theirs is None:
        return cells
    cells = np.r_[cells, edge_cells(document_edges(song, ours), theirs)]
    _, first = np.unique(cells[:, :2], axis=0, return_index=True)
    return cells[first]


def checked(text_cells: tuple, frames: int) -> str:
    """The text of ``(cells, text)``, or a Divergence naming the first differing cell."""
    cells, text = text_cells
    if cells.size:
        frame, reg = (int(c) for c in cells[0][:2])
        raise Divergence(f"frame {frame} register {reg}: {len(cells)} cells over {frames} frames")
    return text


def lifted_text(verify, raw: bytes, subtune: int, frames: int | None, default: int) -> str:
    """The text of ``verify(raw, subtune, frames)`` (``default`` frames where None), or a
    Divergence naming the first differing cell."""
    frames = frames or default
    return checked(verify(raw, subtune, frames), frames)
