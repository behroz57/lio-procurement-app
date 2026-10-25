"""Text normalisation shared by commodity rules and article suggestions."""
import re


def singular(word: str) -> str:
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    # Words are singularized alike everywhere, so "cable ties" matches "cable tie".
    return [singular(word) for word in re.findall(r"[a-z0-9]+", text.lower())]
