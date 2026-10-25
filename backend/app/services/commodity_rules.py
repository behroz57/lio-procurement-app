import re
from collections.abc import Iterable

from app.schemas.organizations import CommodityRule

# Small rounding margin so a total of exactly max_total always matches.
_TOTAL_EPSILON = 1e-9


def _singular(word: str) -> str:
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    # Keywords and text are singularized alike, so "cable ties" matches "cable tie".
    return [_singular(word) for word in re.findall(r"[a-z0-9]+", text.lower())]


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
    text_tokens = [_tokens(text) for text in texts if text]
    for position, rule in enumerate(rules, start=1):
        if rule.max_total is not None and (
            total is None or round(total, 2) > rule.max_total + _TOTAL_EPSILON
        ):
            continue
        by_keyword = any(
            _contains(tokens, _tokens(keyword))
            for keyword in rule.keywords
            for tokens in text_tokens
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
