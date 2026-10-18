"""Which table entries a song can ever read, found by running it to a state recurrence."""

from tqdm import tqdm

from unitracker.player.vm import SongPlayer

MAX_TICKS = 1_000_000
STRIDE, REMEMBERED = 8, 1 << 14


class ReachabilityError(ValueError):
    """The song's playback state did not recur within the tick bound."""


def recurrence(player, max_ticks: int = MAX_TICKS, progress: bool = False, each=None):
    """``(tick, period)``: a tick whose complete state equals the one ``period`` before.

    Playback is deterministic, so the state from ``tick - period`` on repeats with that period.
    Every ``STRIDE``th state of the first ``STRIDE * REMEMBERED`` ticks is kept, so a recurrence
    among them is found within ``STRIDE`` ticks of the earliest; past them Brent's algorithm
    compares each tick's state with one saved state. ``each(player)`` runs after every tick, and
    a true result stops the run, returning None.
    """
    kept, saved, power, period = {}, None, 1, 0
    for tick in tqdm(range(max_ticks + 1), desc="reach", unit="tick", disable=not progress):
        player.play_frame()
        if each is not None and each(player):
            return None
        state = player.state()
        if saved is None:
            before = kept.get(state)
            if before is not None:
                return tick, tick - before
            if tick % STRIDE == 0:
                kept[state] = tick
            if len(kept) == REMEMBERED:
                kept, saved = {}, state
            continue
        period += 1
        if state == saved:
            return tick, period
        if period == power:
            saved, power, period = state, power * 2, 0
    raise ReachabilityError(f"playback state did not recur within {max_ticks} ticks")


def table_reads(ir: dict, max_ticks: int = MAX_TICKS, progress: bool = False):
    """``({table: indices read}, ticks run, period)`` over every state the song reaches."""
    player = SongPlayer(ir, record_reads=True)
    tick, period = recurrence(player, max_ticks, progress)
    return player.table_reads, tick, period
