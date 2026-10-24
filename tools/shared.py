"""What the tools share: tunes by path, HVSC name or tune list."""

import csv
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = (
    "MUSICIANS/C/Cadaver/Metal_Warrior_Title_Remix.sid",
    "MUSICIANS/A/Arturo_Dente/Mega-Run.sid",
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
    survey ``find`` list or an earlier result table) and of directories' ``.sid`` files (with
    their md5), the lists' rows filtered by their SIDId ``player`` (a regex) and ``songs``."""
    out = []
    for source in sources:
        if Path(source).is_dir():
            out += [
                {"path": str(p), "md5": hashlib.md5(p.read_bytes()).hexdigest()}
                for p in sorted(Path(source).rglob("*.sid"))
            ]
            continue
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
