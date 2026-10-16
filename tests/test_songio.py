"""YAML documents: key order on write, any order on read."""

from unitracker import songio


def test_dumps_orders_keys_and_loads_any_order():
    song = {"phrases": {}, "extra": 1, "playroutine": {}, "unitracker": 0, "tables": {}}
    text = songio.dumps(song)
    assert list(songio.loads(text)) == ["unitracker", "tables", "playroutine", "phrases", "extra"]
    assert songio.loads(text) == song
