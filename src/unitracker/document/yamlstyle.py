"""The YAML style of the generic document: block structure, flow leaves, plain atoms."""

import re

import yaml


class Flow(list):
    """A list written in flow style."""


class FlowMap(dict):
    """A mapping written in flow style."""


class Atom(str):
    """A plain scalar written as is: event atoms, names, entries."""


class Dumper(yaml.SafeDumper):
    """Block structure, flow leaves, and atoms as unquoted plain scalars."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

    def choose_scalar_style(self):
        if self.event.style == "A":
            return ""
        return super().choose_scalar_style()


_SIGNED = re.compile(r"^[+-]\d+$")
_DECIMAL = re.compile(r"^\d+\.\d+$")


def _atom(dumper, data):
    if data in ("off", "on"):
        tag = "tag:yaml.org,2002:bool"
    elif _SIGNED.match(data):
        tag = "tag:yaml.org,2002:int"
    elif _DECIMAL.match(data):
        tag = "tag:yaml.org,2002:float"
    else:
        tag = "tag:yaml.org,2002:str"
    return dumper.represent_scalar(tag, str(data), style="A")


Dumper.add_representer(Flow, lambda d, x: d.represent_sequence("tag:yaml.org,2002:seq", x, True))
Dumper.add_representer(FlowMap, lambda d, x: d.represent_mapping("tag:yaml.org,2002:map", x, True))
Dumper.add_representer(Atom, _atom)
Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "~"))
