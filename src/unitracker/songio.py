"""Read and write unitracker YAML documents."""

import yaml

FLOW_WIDTH = 96
KEY_ORDER = (
    "unitracker",
    "meta",
    "clock",
    "voices",
    "registers",
    "tables",
    "playroutine",
    "records",
    "tracks",
    "phrases",
)


class _Dumper(yaml.SafeDumper):
    """Flow style for any collection that fits on one line, block style otherwise."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _flow_fits(data) -> bool:
    text = yaml.safe_dump(data, default_flow_style=True, width=1 << 30, sort_keys=False)
    return len(text) <= FLOW_WIDTH and "\n" not in text.strip()


def _dict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=_flow_fits(data))


def _list(dumper, data):
    flow = all(isinstance(v, int) for v in data) or _flow_fits(data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(dict, _dict)
_Dumper.add_representer(list, _list)


def song_meta(header, source: str) -> dict:
    """The ``meta`` mapping for a tune with PSID ``header``, lifted by ``source``."""
    return {
        "title": header.name,
        "author": header.author,
        "released": header.released,
        "source": source,
    }


def ordered(song: dict) -> dict:
    """``song`` with its top-level keys in :data:`KEY_ORDER`, any others after."""
    out = {k: song[k] for k in KEY_ORDER if k in song}
    out.update((k, v) for k, v in song.items() if k not in out)
    return out


def dumps(song: dict) -> str:
    """YAML text for ``song``: player build first, then the playroutine, then the song."""
    return yaml.dump(
        ordered(song), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=FLOW_WIDTH
    )


def loads(text: str) -> dict:
    """A song dict from YAML text."""
    return yaml.safe_load(text)


def load(path) -> dict:
    """A song dict from a YAML file."""
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


def dump(song: dict, path) -> None:
    """Write ``song`` as YAML to ``path``."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(song))
