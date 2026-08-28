from decimal import Decimal

import pytest
from bp_engine.execution.service import (
    PaperCashInvariantError,
    derive_paper_cash,
    settlement_payout,
)


def test_derive_paper_cash_uses_fill_costs_and_realized_payouts_only() -> None:
    cash = derive_paper_cash(
        starting_cash=Decimal("100"),
        fill_costs=(Decimal("3.25"), Decimal("1.75")),
        settlement_payouts=(Decimal("2.00"),),
    )

    assert cash == Decimal("97.00")


def test_derive_paper_cash_fails_closed_on_negative_balance() -> None:
    with pytest.raises(PaperCashInvariantError, match="negative"):
        derive_paper_cash(
            starting_cash=Decimal("5"),
            fill_costs=(Decimal("5.01"),),
            settlement_payouts=(),
        )


def test_settlement_payout_is_binary_and_side_specific() -> None:
    shares = Decimal("8.125")

    assert settlement_payout(shares, selected_side="up", official_outcome="Up") == shares
    assert settlement_payout(shares, selected_side="down", official_outcome="Down") == shares
    assert settlement_payout(shares, selected_side="up", official_outcome="Down") == Decimal("0")
    assert settlement_payout(shares, selected_side="down", official_outcome="Up") == Decimal("0")

    with pytest.raises(ValueError, match="official_outcome"):
        settlement_payout(shares, selected_side="up", official_outcome="Unknown")
