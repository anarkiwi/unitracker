"""A song's ``playroutine.tables`` patch: its differences from the library, kept to what
the song reads."""

from unitracker.format.yamlstyle import Flow, FlowMap
from unitracker.player.reach import ReachabilityError, table_reads


def table_diff(tables, defaults) -> dict:
    """Tables as sparse ``{index: value}`` differences from the library defaults."""
    out = {}
    for name, values in tables.items():
        base = [int(x) for x in defaults.get(name) or []]
        if len(base) != len(values):
            out[name] = Flow(values)
            continue
        diff = FlowMap({i: v for i, (v, d) in enumerate(zip(values, base)) if v != d})
        if diff:
            out[name] = diff
    return out


def unread_dropped(diff: dict, ir: dict) -> dict:
    """``diff`` without the patch entries the song never reads.

    An entry no reachable tick reads cannot change the output, so the reader's library
    default stands in for it. If reachability can't be established the patch is kept whole.
    """
    if not diff:
        return diff
    try:
        reads = table_reads(ir)[0]
    except ReachabilityError:
        return diff
    out = {}
    for name, values in diff.items():
        if isinstance(values, Flow):
            out[name] = values
            continue
        kept = FlowMap({i: v for i, v in values.items() if i in reads.get(name, ())})
        if kept:
            out[name] = kept
    return out
