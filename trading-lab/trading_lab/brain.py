"""The brain: Claude Opus 5.5 as a slow escalation desk. Never on the entry path.

JevStrategy calls it when Jev's confidence drops below the escalation threshold or the regime is
crisis. The answer is constrained by a JSON schema to risk-REDUCING actions only: it can leave
things alone, pause new entries, or flatten. It cannot open, enlarge, or change any limit.
Any failure (SDK missing, no credentials, network, refusal, bad JSON) resolves to "pause".

Needs `pip install anthropic` and ANTHROPIC_API_KEY. Everything else in the lab runs without it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MODEL = "claude-opus-5-5"

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["no_change", "pause", "flatten"]},
        "pause_hours": {"type": "integer"},
        "reasoning": {"type": "string"},
        "what_would_change_my_mind": {"type": "string"},
    },
    "required": ["action", "pause_hours", "reasoning", "what_would_change_my_mind"],
    "additionalProperties": False,
}

SYSTEM = (
    "You review escalations for a small, long-only, spot crypto paper-trading system. A fast decision model "
    "(Jev) reported low confidence or a crisis regime. You get the market snapshot, Jev's typed answers, "
    "a calibrated probability, and the current exposure. Decide whether the system should keep following "
    "its normal rules (no_change), stop opening new positions for pause_hours (pause, 1 to 24), or close "
    "its position now (flatten). You cannot open or enlarge positions or change limits. When the evidence "
    "is ambiguous, prefer pause: surviving matters more than any single trade."
)


@dataclass(frozen=True)
class Verdict:
    action: str
    pause_hours: int
    reasoning: str
    source: str  # "opus" | "fallback"

    @classmethod
    def fail_closed(cls, why: str) -> "Verdict":
        return cls("pause", 4, f"fail-closed: {why}", "fallback")


class Brain:
    def __init__(self, model: str = MODEL, effort: str = "high", timeout: float = 120.0):
        self.model, self.effort, self.timeout = model, effort, timeout
        try:
            import anthropic
            self._anthropic = anthropic
            self._client = anthropic.Anthropic()
        except Exception as e:  # SDK not installed, or no credentials found
            self._anthropic, self._client, self._init_error = None, None, repr(e)

    @property
    def available(self) -> bool:
        return self._client is not None

    def review(self, packet: dict) -> Verdict:
        if self._client is None:
            return Verdict.fail_closed(f"brain unavailable: {self._init_error}")
        a = self._anthropic
        try:
            resp = self._client.with_options(timeout=self.timeout).messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(packet, sort_keys=True)}],
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            )
        except a.RateLimitError:
            return Verdict.fail_closed("rate limited")
        except a.APIStatusError as e:
            return Verdict.fail_closed(f"API error {e.status_code}")
        except a.APIConnectionError:
            return Verdict.fail_closed("connection error")
        if resp.stop_reason == "refusal":
            return Verdict.fail_closed("model declined")
        if resp.stop_reason == "max_tokens":
            return Verdict.fail_closed("response truncated")
        try:
            data = json.loads(next(b.text for b in resp.content if b.type == "text"))
            action = data["action"]
            if action not in ("no_change", "pause", "flatten"):
                raise ValueError(action)
            hours = max(0, min(int(data["pause_hours"]), 24))
            return Verdict(action, hours, str(data["reasoning"])[:2000], "opus")
        except (StopIteration, KeyError, TypeError, ValueError) as e:
            return Verdict.fail_closed(f"unparseable verdict: {e!r}")
