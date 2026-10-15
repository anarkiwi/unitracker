"""Read and write unitracker YAML documents."""

import yaml

FLOW_WIDTH = 96


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


def dumps(song: dict) -> str:
    """YAML text for ``song``."""
    return yaml.dump(song, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=FLOW_WIDTH)


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
