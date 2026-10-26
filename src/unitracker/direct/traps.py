"""The frequency-table reads a native player answers differently from the document's ``F(k)``."""

from typing import NamedTuple


class Trap(NamedTuple):
    """A read of the player's frequency table at index ``k`` by ``voice`` on ``tick`` whose
    ``native`` word is not ``word``, the document's ``F(k)``; the reference answered ``word``."""

    tick: int
    voice: int
    k: int
    native: int
    word: int


class Log:
    """The traps of one run: ``tick`` advances with each of the player's frames; a read the
    native and document words agree on is not logged, nor is one repeated in a tick."""

    def __init__(self):
        self.tick = -1
        self.traps = {}

    def frames(self, ref, name: str = "_frame"):
        """Count ``ref``'s frames (its method ``name``) as ticks."""
        frame = getattr(ref, name)

        def counted(*args):
            self.tick += 1
            return frame(*args)

        setattr(ref, name, counted)

    def read(self, voice: int, k: int, native: int, word: int) -> int:
        """Log the read if the words differ; ``word``, the reference's answer."""
        if native != word:
            self.traps.setdefault((self.tick, voice, k), Trap(self.tick, voice, k, native, word))
        return word

    def __call__(self) -> tuple:
        return tuple(self.traps.values())
