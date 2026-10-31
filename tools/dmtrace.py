"""Trace a defMON tune's spec-player state cell by cell: ``dmtrace.py TUNE FRAMES CELL...`` prints
each frame's value of the layout cells named (per-voice cells with ``@v``)."""

import sys

from pydefmon import DefmonSong
from pydefmon.spec import VOICE_STRIDE, build_facts, player_bases
from pysidtracker import find_format
from pysidtracker.testing import resolve_tune
from tqdm import tqdm


def trace(raw: bytes, frames: int, cells: list, subtune: int = 0):
    """The rows ``(frame, values...)`` of the layout ``cells`` after each frame; ``name@v`` is
    voice ``v``'s cell."""
    song = DefmonSong.from_sid_bytes(raw)
    song.subtune = subtune
    player = find_format(raw).spec_player(song, notes=True)
    mem = player.memory
    binding = build_facts(mem, player_bases(mem)[0], player.entry).binding
    where = []
    for cell in cells:
        name, _, voice = cell.partition("@")
        where.append(binding[name] + (int(voice) * VOICE_STRIDE if voice else 0))
    for frame in tqdm(range(frames), unit="frame", disable=frames < 500):
        yield (frame, *(player.memory[a] for a in where))
        player.play_frame()


def main(argv=None) -> int:
    """Print the trace."""
    argv = sys.argv[1:] if argv is None else argv
    raw = resolve_tune(argv[0]).read_bytes()
    print("frame", *argv[2:])
    for row in trace(raw, int(argv[1]), argv[2:]):
        print(*row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
