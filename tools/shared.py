"""What the tools share: tunes by path, HVSC name or tune list, lifts, and profiles."""

import cProfile
import csv
import os
import pstats
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/H/Hermit/Csillag_szazketto_kettoskereszt.sid",
    "MUSICIANS/I/Ilkke/Crosseyed.sid",
)


def tune(source, hvsc=None) -> Path:
    """The ``.sid`` file ``source``: a local path, else a path under ``hvsc``, else an HVSC name
    resolved through ``$HVSC``, pysidtracker's tune cache or the mirror."""
    path = Path(source)
    if path.is_file():
        return path
    if hvsc is not None and (Path(hvsc) / source).is_file():
        return Path(hvsc) / source
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    found = resolve_tune(str(source))
    if found is None:
        raise FileNotFoundError(f"{source}: no such tune")
    return Path(found)


def name(row: dict) -> str:
    """A tune's output name: its file stem, after its md5's first 8 digits when it has one."""
    stem = Path(row["path"]).stem
    return f"{row['md5'][:8]}-{stem}" if row.get("md5") else stem


def rows(sources, player: str = "", subtunes: int | None = None) -> list:
    """Tune rows (``path`` and any other columns) of tune paths and of ``.csv`` tune lists (a
    survey ``find`` list or an earlier result table), the lists' rows filtered by their SIDId
    ``player`` (a regex) and their count of ``songs``."""
    out = []
    for source in sources:
        if not str(source).endswith(".csv"):
            out.append({"path": str(source)})
            continue
        with open(source, newline="", encoding="utf-8") as fh:
            out += [
                r
                for r in csv.DictReader(fh)
                if re.search(player, r.get("player", ""), re.I)
                and (subtunes is None or int(r.get("songs") or 1) <= subtunes)
            ]
    return out


def lift(source, subtune=None, expressions: bool = True) -> dict:
    """The lifter's song of a tune (with ``expressions``, converted; else its native
    modulations)."""
    from unitracker.lift import backends  # pylint: disable=import-outside-toplevel

    return backends.lift(tune(source).read_bytes(), subtune, expressions=expressions)


def single_worker() -> None:
    """Keep a conversion in this process (the caller parallelises over tunes)."""
    os.environ["UNITRACKER_WORKERS"] = "1"


def profile_report(prof: cProfile.Profile, out=None, top: int = 30) -> None:
    """Print a profile's top functions by cumulative and by own time; write it to ``out``."""
    if out:
        prof.dump_stats(out)
    for order in ("cumulative", "tottime"):
        pstats.Stats(prof, stream=sys.stdout).sort_stats(order).print_stats(top)
