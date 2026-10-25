"""Catalog article suggestion scoring (pure functions, no DB).

Run with: python -m pytest tests/ -q
"""
from decimal import Decimal
from types import SimpleNamespace

from app.services.article_suggestions import content_words, rank_articles


def _article(number, description, price):
    return SimpleNamespace(
        article_number=number, description=description, unit_price=Decimal(str(price))
    )


CATALOG = [
    _article("T-1", "Toner cartridge black, high-yield", 139),
    _article("T-2", "Toner cartridge black, high-yield", 120),
    _article("T-3", "Toner cartridge cyan", 95),
    _article("C-1", "Office chair ergonomic mesh", 181),
    _article("C-2", "Office chair executive leather", 146),
    _article("O-1", "Office desk standing", 420),
    _article("O-2", "Office whiteboard magnetic", 42),
    _article("O-3", "Office shredder cross-cut", 160),
    _article("P-1", "Patch cable Cat 6 3 ft", 23),
    _article("P-2", "Charging cable J1772 16 ft", 150),
    _article("A-1", "Void fill air pillows", 18),
]


def _numbers(query, catalog=CATALOG):
    return [article.article_number for _, article in rank_articles(query, catalog)]


def test_filler_words_numbers_and_units_are_ignored():
    assert content_words("Cable ties 200 pcs, 3 ft x 2 in, with clips") == ["cable", "tie", "clip"]


def test_rare_words_weigh_more_than_common_ones():
    # "office" is in five articles, "mesh" in one: matching the rare word scores higher.
    scores = {a.article_number: s for s, a in rank_articles("office mesh", CATALOG)}
    assert scores["C-1"] == 1.0
    assert all(score < 1.0 for number, score in scores.items() if number != "C-1")


def test_item_word_that_names_the_article_is_enough():
    # "printer" is not in the catalog, but "toner" is the item and names the articles.
    assert set(_numbers("printer toner")) == {"T-1", "T-2", "T-3"}


def test_weak_matches_return_nothing():
    # One incidental word: the item ("tie", "air") is not what the articles are.
    assert _numbers("cable ties") == []
    assert _numbers("MacBook Air") == []
    assert _numbers("whiteboard markers") == []


def test_ranked_by_score_then_cheapest():
    assert _numbers("toner cartridge black high yield")[:2] == ["T-2", "T-1"]
    assert _numbers("toner") == ["T-3", "T-2", "T-1"]


def test_no_match_or_no_words_returns_nothing():
    assert _numbers("coffee beans") == []
    assert _numbers("") == []
    assert _numbers("3 pcs x 2 in") == []
    assert _numbers("toner", catalog=[]) == []
