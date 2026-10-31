"""What the format lifts share: their errors, definition names, the check against a reference
grid, note names, quoted meta values, bare rest runs and repeat detection."""

import re

import numpy as np
from pysidtracker import gridcmp
from unitracker_grammar.document import Call, Event
from unitracker_player.player import play

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


def cells_of(text: str, frames: int, grid=None, reference=None) -> np.ndarray:
    """The cells where the document ``text`` played over ``frames`` frames differs from ``grid``,
    or from ``reference(n)`` over the ``n`` frames it played."""
    ours = np.asarray(play(text, frames), dtype=np.uint8)
    if grid is None:
        grid = np.asarray(reference(len(ours)), dtype=np.uint8)
    return gridcmp.cell_diff(ours, grid[: len(ours)])


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
