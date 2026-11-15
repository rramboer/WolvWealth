"""Progress reporting for long-running backtests."""

import dataclasses
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class Progress:
    """How far a backtest has got.

    Steps are optimizer rebalance decisions (the slow part, about a second
    each) followed by one step per benchmark run (fast), so `fraction` tracks
    wall-clock time closely enough to drive an ETA.
    """

    phase: str  # "optimizer" or "benchmarks"
    completed: int
    total: int
    message: str

    @property
    def fraction(self) -> float:
        """Completed share of the work, 0..1."""
        return self.completed / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        """JSON-friendly representation."""
        return {
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "fraction": round(self.fraction, 4),
            "message": self.message,
        }


ProgressCallback = Callable[[Progress], None]
