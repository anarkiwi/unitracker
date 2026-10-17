"""The YAML style of a written document: block structure, flow leaves, plain atoms."""

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


class Decimal(float):
    """A decimal number that keeps its text: a version's digits after the point are a number
    of their own (1.10 follows 1.9)."""

    text = ""


def _decimal(loader, node):
    out = Decimal(loader.construct_yaml_float(node))
    out.text = str(node.value)
    return out


class Loader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """Safe loading with each decimal number's text kept."""


Loader.add_constructor("tag:yaml.org,2002:float", _decimal)
for _dumper in (yaml.SafeDumper, Dumper):
    _dumper.add_representer(
        Decimal, lambda d, x: d.represent_scalar("tag:yaml.org,2002:float", x.text)
    )


def parse(text: str):
    """A document's YAML, its version as written."""
    return yaml.load(text, Loader=Loader)


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
