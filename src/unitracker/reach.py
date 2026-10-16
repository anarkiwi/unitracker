"""Which table entries a song can ever read, found by running it to a state recurrence."""

from tqdm import tqdm

from unitracker.vm import SongPlayer

MAX_TICKS = 1_000_000


class ReachabilityError(ValueError):
    """The song's playback state did not recur within the tick bound."""


def table_reads(ir: dict, max_ticks: int = MAX_TICKS, progress: bool = False):
    """``({table: indices read}, ticks run, period)`` over every state the song reaches.

    Playback is deterministic, so once a complete state recurs every later tick repeats
    one already run, and the recorded reads are all the song can make. Recurrence is
    found with Brent's algorithm, comparing each tick's state with one saved state.
    """
    player = SongPlayer(ir, record_reads=True)
    player.play_frame()
    saved = player.state()
    power = period = 1
    for tick in tqdm(range(1, max_ticks + 1), desc="reach", unit="tick", disable=not progress):
        player.play_frame()
        state = player.state()
        if state == saved:
            return player.table_reads, tick, period
        if period == power:
            saved, power, period = state, power * 2, 0
        period += 1
    raise ReachabilityError(f"playback state did not recur within {max_ticks} ticks")
