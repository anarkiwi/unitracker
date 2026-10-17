"""Which table entries a song can ever read, found by running it to a state recurrence."""

from tqdm import tqdm

from unitracker.player.vm import SongPlayer

MAX_TICKS = 1_000_000


class ReachabilityError(ValueError):
    """The song's playback state did not recur within the tick bound."""


def recurrence(player, max_ticks: int = MAX_TICKS, progress: bool = False, each=None):
    """``(tick, period)``: the first tick whose complete state equals the one ``period`` before.

    Playback is deterministic, so the state from ``tick - period`` on repeats with that period;
    Brent's algorithm compares each tick's state with one saved state. ``each(player)`` runs
    after every tick, and a true result stops the run, returning None.
    """
    player.play_frame()
    if each is not None and each(player):
        return None
    saved = player.state()
    power = period = 1
    for tick in tqdm(range(1, max_ticks + 1), desc="reach", unit="tick", disable=not progress):
        player.play_frame()
        if each is not None and each(player):
            return None
        state = player.state()
        if state == saved:
            return tick, period
        if period == power:
            saved, power, period = state, power * 2, 0
        period += 1
    raise ReachabilityError(f"playback state did not recur within {max_ticks} ticks")


def table_reads(ir: dict, max_ticks: int = MAX_TICKS, progress: bool = False):
    """``({table: indices read}, ticks run, period)`` over every state the song reaches."""
    player = SongPlayer(ir, record_reads=True)
    tick, period = recurrence(player, max_ticks, progress)
    return player.table_reads, tick, period
