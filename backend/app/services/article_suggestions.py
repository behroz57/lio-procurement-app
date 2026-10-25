"""Rank an organization's catalog articles against an order line description."""
import math
from collections import Counter
from typing import Protocol

from app.services.text_match import tokens

# Words that say nothing about the product, including quantity and unit filler.
FILLER_WORDS = frozenset({
    "a", "an", "and", "or", "the", "of", "for", "with", "without", "in", "on", "at", "to",
    "from", "by", "per", "x", "incl", "including", "plus", "approx", "pc", "piece", "ea",
    "each", "unit", "qty", "item", "no", "nr",
    "inch", "ft", "foot", "feet", "yd", "mm", "cm", "km", "lb", "kg", "oz", "gal", "ml", "sq",
})
STRONG_COVERAGE = 0.6
MIN_COVERAGE = 0.35


class _Article(Protocol):
    article_number: str
    description: str
    unit_price: object


def content_words(text: str) -> list[str]:
    return [
        word for word in tokens(text)
        if len(word) > 1 and not word.isdigit() and word not in FILLER_WORDS
    ]


def rank_articles(query: str, articles: list[_Article]) -> list[tuple[float, _Article]]:
    """Return (score, article) for every acceptable match, best first.

    The score is the share of the query's words found in the article, each word
    weighted by how rare it is in this catalog; a word the catalog never uses
    weighs the most, so a query about something the catalog lacks scores low.
    """
    ordered_query_words = content_words(query)
    if not ordered_query_words or not articles:
        return []
    query_words = set(ordered_query_words)
    # The last word usually names the item ("printer toner" is toner, not a printer).
    item_word = ordered_query_words[-1]

    article_words = [content_words(article.description) for article in articles]
    document_frequency = Counter(word for words in article_words for word in set(words))
    size = len(articles)
    weight = {
        word: math.log((size + 1) / (document_frequency[word] + 1)) + 1 for word in query_words
    }
    total_weight = sum(weight.values())

    scored = []
    for article, words in zip(articles, article_words):
        matched = query_words.intersection(words)
        if not matched:
            continue
        score = sum(weight[word] for word in matched) / total_weight
        # A partial match needs more than one incidental word: either two query words
        # match, or the query's item word is the word the article's name starts with.
        same_item = item_word in matched and words[0] == item_word
        if score >= STRONG_COVERAGE or (
            score >= MIN_COVERAGE and (same_item or len(matched) >= 2)
        ):
            scored.append((score, article))

    scored.sort(key=lambda item: (-item[0], item[1].unit_price, item[1].article_number))
    return scored
