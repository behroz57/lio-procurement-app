"""Automatic approval rule (pure functions, no DB).

Run with: python -m pytest tests/ -q
"""
from app.services.approval import auto_approval_summary, format_usd, is_auto_approved


def test_strictly_below_the_limit_is_auto_approved():
    assert is_auto_approved(150, 2000)
    assert is_auto_approved(1999.99, 2000)
    assert not is_auto_approved(2000.00, 2000)
    assert not is_auto_approved(5000, 2000)


def test_no_limit_means_everything_needs_approval():
    assert not is_auto_approved(150, None)
    assert not is_auto_approved(0.01, None)


def test_total_is_compared_in_cents():
    assert is_auto_approved(1999.994, 2000)
    assert not is_auto_approved(1999.996, 2000)


def test_summary_names_total_and_limit():
    assert format_usd(2000) == "$2,000"
    assert format_usd(1999.99) == "$1,999.99"
    assert auto_approval_summary(150, 2000) == (
        "Auto-approved: total $150 below organization limit $2,000"
    )
