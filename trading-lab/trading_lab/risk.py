"""Deterministic risk engine. Every target exposure passes through here before any fill.

Nothing upstream (strategy, model, human in a hurry) can raise these limits at runtime:
they are read once from RiskLimits and applied in order, most severe first.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    max_exposure: float = 1.0      # 1.0 = fully invested, never leveraged
    max_daily_loss: float = 0.05   # flatten for the rest of the UTC day after -5%
    max_drawdown: float = 0.20     # flatten and halt until a human resets after -20% from peak
    kill_file: str = "KILL"        # if this file exists, flatten and do nothing else


@dataclass
class RiskDecision:
    approved: float
    reasons: list[str] = field(default_factory=list)


class RiskEngine:
    def __init__(self, limits: RiskLimits, halted: bool = False):
        self.limits = limits
        self.halted = halted  # latched by the drawdown breaker; only reset() clears it

    def reset(self) -> None:
        self.halted = False

    def check(self, target: float, equity: float, peak_equity: float, day_start_equity: float) -> RiskDecision:
        lim = self.limits
        if os.path.exists(lim.kill_file):
            return RiskDecision(0.0, ["kill_switch"])
        if self.halted:
            return RiskDecision(0.0, ["halted_max_drawdown"])
        if peak_equity > 0 and 1 - equity / peak_equity >= lim.max_drawdown:
            self.halted = True
            return RiskDecision(0.0, ["max_drawdown_breached"])
        if day_start_equity > 0 and 1 - equity / day_start_equity >= lim.max_daily_loss:
            return RiskDecision(0.0, ["max_daily_loss"])

        reasons = []
        if not 0.0 <= target <= lim.max_exposure:
            reasons.append(f"clipped_from_{target:.3f}")
        return RiskDecision(min(max(target, 0.0), lim.max_exposure), reasons)
