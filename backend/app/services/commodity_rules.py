from collections.abc import Iterable

from app.schemas.organizations import CommodityRule
from app.services.text_match import tokens

# Small rounding margin so a total of exactly max_total always matches.
_TOTAL_EPSILON = 1e-9


def _contains(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return False
    size = len(needle)
    return any(haystack[i:i + size] == needle for i in range(len(haystack) - size + 1))


def rules_from_settings(settings: dict | None) -> list[CommodityRule]:
    return [CommodityRule.model_validate(r) for r in (settings or {}).get("commodity_rules", [])]


def match_rule(
    rules: list[CommodityRule],
    texts: Iterable[str],
    total: float | None,
    current_group_id: int | None,
) -> tuple[int, CommodityRule] | None:
    """Return (1-based position, rule) of the first matching rule, or None."""
    text_words = [tokens(text) for text in texts if text]
    for position, rule in enumerate(rules, start=1):
        if rule.max_total is not None and (
            total is None or round(total, 2) > rule.max_total + _TOTAL_EPSILON
        ):
            continue
        by_keyword = any(
            _contains(words, tokens(keyword))
            for keyword in rule.keywords
            for words in text_words
        )
        if by_keyword or current_group_id in rule.from_commodity_group_ids:
            return position, rule
    return None


def describe(position: int, rule: CommodityRule) -> str:
    parts = []
    if rule.keywords:
        parts.append("keywords: " + ", ".join(rule.keywords))
    if rule.from_commodity_group_ids:
        parts.append("from groups: " + ", ".join(map(str, rule.from_commodity_group_ids)))
    if rule.max_total is not None:
        parts.append(f"total up to {rule.max_total:.2f}")
    return f"rule {position} ({'; '.join(parts)})"
