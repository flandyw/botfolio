"""Transaction-cost model: spread, slippage, commissions and US regulatory fees."""

from __future__ import annotations

from typing import Literal

from ttbot.config import CostConfig

OrderType = Literal["market", "stop", "limit"]


class CostModel:
    def __init__(self, cfg: CostConfig) -> None:
        self.cfg = cfg

    def fill_price(self, ref_price: float, side: int, order_type: OrderType) -> float:
        """Price actually paid/received for a trade of direction ``side`` (+1 buy, -1 sell).

        market: crosses half the spread plus ``slippage_bps``.
        stop:   as market plus ``stop_slippage_bps`` (stops trigger into momentum).
        limit:  fills exactly at the limit price (only when price trades THROUGH it, see portfolio).
        """
        if order_type == "limit":
            return ref_price
        bps = self.cfg.slippage_bps + (self.cfg.stop_slippage_bps if order_type == "stop" else 0.0)
        adverse = self.cfg.half_spread_per_share + ref_price * bps / 10_000.0
        return ref_price + side * adverse

    def fees(self, qty: int, price: float, side: int) -> float:
        """Commission plus SEC Section 31 + FINRA TAF fees (sell side only)."""
        c = self.cfg
        commission = max(c.commission_per_share * qty, c.min_commission) if qty > 0 else 0.0
        reg = 0.0
        if side < 0:
            reg += qty * price * c.sec_fee_per_million / 1_000_000.0
            reg += min(qty * c.finra_taf_per_share, c.finra_taf_max)
        return commission + reg
