"""unitracker's generic optimiser: any canonical document rewritten smaller, playing the same
register grid on unitracker-player (docs/optimiser.md)."""

from unitracker.optimiser.driver import PASSES, Result, optimise, size
from unitracker.optimiser.guarantee import Guarantee, GuaranteeError

__all__ = ["PASSES", "Guarantee", "GuaranteeError", "Result", "optimise", "size"]
