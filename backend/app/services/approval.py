"""Automatic approval of requests below an organization's limit."""


def is_auto_approved(total: float, limit: float | None) -> bool:
    # Strictly below: a total equal to the limit still needs a buyer.
    return limit is not None and round(total, 2) < limit


def format_usd(value: float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def auto_approval_summary(total: float, limit: float) -> str:
    return f"Auto-approved: total {format_usd(total)} below organization limit {format_usd(limit)}"
