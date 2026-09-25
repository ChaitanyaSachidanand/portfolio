"""Paper broker: simulated spot account with fees, slippage and minimum order size."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001        # 0.10% per side, a typical spot taker fee
    slippage_bps: float = 5.0      # 0.05% worse than the reference price
    min_notional: float = 5.0      # exchanges reject tiny orders
    rebalance_band: float = 0.10   # ignore changes smaller than 10% of equity (stops fee churn)


@dataclass
class Fill:
    side: str
    qty: float
    expected_price: float
    fill_price: float
    notional: float
    fee: float


class PaperBroker:
    def __init__(self, cash: float, costs: CostModel, qty: float = 0.0):
        self.cash = cash
        self.qty = qty
        self.costs = costs
        self.fees_paid = 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def exposure(self, price: float) -> float:
        eq = self.equity(price)
        return 0.0 if eq <= 0 else self.qty * price / eq

    def rebalance(self, target: float, price: float) -> Fill | None:
        c = self.costs
        eq = self.equity(price)
        delta = target * eq - self.qty * price
        full_exit = target == 0.0 and self.qty > 0
        if not full_exit and (abs(delta) < c.min_notional or abs(delta) < c.rebalance_band * eq):
            return None

        slip = c.slippage_bps / 10_000
        if delta > 0:
            fill_price = price * (1 + slip)
            notional = min(delta, self.cash / (1 + c.fee_rate))
            if notional < c.min_notional:
                return None
            qty = notional / fill_price
            fee = notional * c.fee_rate
            self.cash = max(self.cash - notional - fee, 0.0)  # float rounding can't overdraw
            self.qty += qty
            side = "buy"
        else:
            fill_price = price * (1 - slip)
            qty = self.qty if full_exit else min(self.qty, -delta / price)
            notional = qty * fill_price
            fee = notional * c.fee_rate
            self.cash += notional - fee
            self.qty -= qty
            side = "sell"
        self.fees_paid += fee
        return Fill(side, qty, price, fill_price, notional, fee)
