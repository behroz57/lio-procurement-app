"""Check catalog article suggestions against the live API.

Usage (backend must be running):
    python scripts/eval_suggestions.py              # new GET /articles/suggestions
    python scripts/eval_suggestions.py --search     # baseline: existing GET /articles?search=

Standard library only. Read-only: creates nothing.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://localhost:7200"
PASSWORD = "demo1234"
ACME = "requester@acme.com"
WALMART = "admin@walmart.com"

# check: ("top1", number) | ("top3", {numbers}) | ("top3_prefix", text) | ("none",)
CASES = [
    ("printer toner", ACME, ("top3_prefix", "Toner cartridge")),
    ("Toner cartridge black high yield", ACME, ("top1", "MON-00914")),
    ("moss wall panel", ACME, ("top3", {"HAR-00172"})),
    ("Moss panel 70:30 with askLio lettering", ACME, ("top3", {"RED-00196", "HAR-00172"})),
    ("Moss Art Panel, Mixed Moss 63 x 31.5 in", ACME, ("top3", {"RED-00196", "HAR-00172"})),
    ("ergonomic mesh office chair", ACME, ("top1", "DEL-00888")),
    ("copy paper letter", ACME, ("top1", "BEA-01875")),
    ("cable ties", ACME, ("none",)),
    ("MacBook Air", ACME, ("none",)),
    ("Grinding machine HW-GS 450", ACME, ("none",)),
    ("office chair", WALMART, ("none",)),
]


def _get(path: str, token: str | None = None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {path} -> HTTP {exc.code}: {exc.read()[:200]!r}") from None


def sign_in(email: str) -> str:
    body = json.dumps({"email": email, "password": PASSWORD}).encode()
    req = urllib.request.Request(BASE_URL + "/auth/signin", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def top3(query: str, token: str, use_search: bool) -> list[dict]:
    q = urllib.parse.quote(query)
    if use_search:
        return _get(f"/articles?search={q}&limit=3", token)["items"]
    return _get(f"/articles/suggestions?q={q}&limit=3", token)


def passes(check: tuple, results: list[dict]) -> bool:
    kind = check[0]
    numbers = [r["article_number"] for r in results]
    if kind == "none":
        return not results
    if kind == "top1":
        return numbers[:1] == [check[1]]
    if kind == "top3":
        return bool(check[1] & set(numbers[:3]))
    return any(r["description"].startswith(check[1]) for r in results[:3])


def describe(check: tuple) -> str:
    if check[0] == "none":
        return "no suggestions"
    if check[0] == "top1":
        return f"{check[1]} top 1"
    if check[0] == "top3":
        return " or ".join(sorted(check[1])) + " in top 3"
    return f'"{check[1]}" in top 3'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--search", action="store_true",
                        help="baseline: use the existing GET /articles?search=")
    args = parser.parse_args()

    tokens = {email: sign_in(email) for email in {ACME, WALMART}}
    endpoint = "GET /articles?search=" if args.search else "GET /articles/suggestions"
    print(f"Endpoint: {endpoint}\n")
    passed = 0
    for query, user, check in CASES:
        results = top3(query, tokens[user], args.search)
        ok = passes(check, results)
        passed += ok
        who = " (Walmart)" if user == WALMART else ""
        print(f"{'PASS' if ok else 'FAIL'}  {query!r}{who}  expected: {describe(check)}")
        for r in results[:3]:
            print(f"        {r['article_number']}  {r['description']}  "
                  f"{r.get('supplier_name')}  {r['unit_price']} / {r['unit']}")
        if not results:
            print("        (no results)")
    print(f"\nPassed: {passed} of {len(CASES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
