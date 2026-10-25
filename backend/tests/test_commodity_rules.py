"""Commodity rule matching (pure functions, no DB).

Run with: python -m pytest tests/ -q
"""
from app.schemas.organizations import CommodityRule
from app.services.commodity_rules import match_rule

CABLE = CommodityRule(keywords=["cable tie", "zip tie"], commodity_group_id=11)
TONER = CommodityRule(keywords=["toner"], commodity_group_id=29)
SOFTWARE = CommodityRule(
    keywords=["software subscription", "saas", "software license", "software licence"],
    commodity_group_id=15,
    max_total=1000,
    from_commodity_group_ids=[31, 106],
)
RULES = [CABLE, TONER, SOFTWARE]


def _group(texts, total=100.0, current=None, rules=RULES):
    match = match_rule(rules, texts, total, current)
    return match[1].commodity_group_id if match else None


def test_keywords_match_case_insensitive_singular_and_plural():
    assert _group(["Cable Ties 200 mm"]) == 11
    assert _group(["cable tie, black"]) == 11
    assert _group(["ZIP-TIES assorted"]) == 11
    assert _group(["Printer toners, black"]) == 29
    assert _group(["Annual SaaS plan"]) == 15
    assert _group(["Software licenses x5"]) == 15
    # A keyword is whole words, not a substring.
    assert _group(["Cable tidy box"]) is None


def test_keyword_matches_title_or_any_order_line():
    assert _group(["Workshop supplies", "Screws", "Cable ties"]) == 11


def test_max_total_is_inclusive():
    assert _group(["SaaS"], total=1000.00) == 15
    assert _group(["SaaS"], total=1000.01) is None
    assert _group(["SaaS"], total=None) is None


def test_from_group_ids_match_without_a_keyword():
    assert _group(["Design tool"], total=500, current=31) == 15
    assert _group(["Design tool"], total=5000, current=31) is None
    assert _group(["Design tool"], total=500, current=29) is None


def test_first_matching_rule_wins():
    texts = ["Toner and cable ties", "SaaS seat"]
    assert _group(texts) == 11
    assert _group(texts, rules=[SOFTWARE, TONER, CABLE]) == 15


def test_no_match_returns_none():
    assert _group(["Office chairs"], current=15) is None
    assert match_rule([], ["Cable ties"], 10, None) is None
