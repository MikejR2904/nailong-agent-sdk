"""What a session has cost, from the prices you configured, and the budget you set."""

from __future__ import annotations

from decimal import Decimal

from nailong_agent_sdk.agent.cost_model import PriceTable, TokenPrice
from nailong_agent_sdk.starter import Answer, ModelSetup

from .config import CliConfig


class BudgetReached(Exception):
    pass


def price_table(config: CliConfig, setup: ModelSetup) -> PriceTable | None:
    spec = config.prices.get(setup.name)
    if spec is None:
        return None
    price = TokenPrice(
        input_per_million=Decimal(str(spec["input_per_million"])),
        cached_input_per_million=Decimal(str(spec["cached_input_per_million"])),
        output_per_million=Decimal(str(spec["output_per_million"])),
    )
    return PriceTable({(setup.provider, setup.name): price})


class Spending:
    def __init__(self, budget: float | None) -> None:
        self.budget = Decimal(str(budget)) if budget is not None else None
        self.total = Decimal(0)
        self.turns_without_price = 0

    def add(self, answer: Answer) -> None:
        if answer.cost is None:
            self.turns_without_price += 1
            return
        self.total += Decimal(answer.cost["total"])

    def check(self) -> None:
        if self.budget is not None and self.total >= self.budget:
            raise BudgetReached(
                f"The budget of {self.budget} is used up ({self.total} spent); raise "
                "max_budget_usd or start a new run."
            )

    def lines(self, last: Answer | None) -> list[str]:
        lines = [f"  spent this run: {self.total}"]
        if self.budget is not None:
            lines.append(f"  budget: {self.budget} ({self.budget - self.total} left)")
        if last is not None and last.cost is not None:
            lines.append(f"  last turn: {last.cost}")
        if self.turns_without_price:
            lines.append(
                f"  {self.turns_without_price} turn(s) had no price: add [prices.MODEL] to your "
                "settings to count them"
            )
        return lines
