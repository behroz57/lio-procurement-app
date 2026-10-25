"""Check automatic approval below the organization's limit against the live API.

Creates requests (and updates some), records approval_status, then deletes
everything it created. Standard library only. No OpenAI key needed.

Usage (backend must be running):
    python scripts/eval_approval.py
"""
import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://localhost:7200"
PASSWORD = "demo1234"
ACME = "requester@acme.com"
WALMART = "admin@walmart.com"

# (name, user, line totals on create, total_cost sent, line totals on update or None, expected)
CASES = [
    ("$150", ACME, [150.0], None, None, "approved"),
    ("$1,999.99", ACME, [1999.99], None, None, "approved"),
    ("$2,000.00", ACME, [2000.0], None, None, "pending"),
    ("$5,000", ACME, [5000.0], None, None, "pending"),
    ("$150 as Walmart", WALMART, [150.0], None, None, "pending"),
    ("lines $5,000, total_cost sent as 1", ACME, [3000.0, 2000.0], 1.0, None, "pending"),
    ("$500, then lines updated to $5,000", ACME, [500.0], None, [5000.0], "pending"),
    ("$5,000, then updated to $500", ACME, [5000.0], None, [500.0], "approved"),
]


def _call(method: str, path: str, token: str | None = None, payload: dict | None = None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from None


def sign_in(email: str) -> str:
    return _call("POST", "/auth/signin", payload={"email": email, "password": PASSWORD})[
        "access_token"
    ]


def _lines(totals: list[float]) -> list[dict]:
    return [
        {"positionDescription": f"Item {i}", "unitPrice": total, "amount": 1,
         "unit": "piece", "totalPrice": total}
        for i, total in enumerate(totals, start=1)
    ]


def _fields(name: str, totals: list[float], total_cost: float | None) -> dict:
    return {
        "requestorName": "Approval eval",
        "vendorName": "Example Supplies LLC",
        "vatId": "12-3456789",
        "commodityGroupId": 15,
        "totalCost": sum(totals) if total_cost is None else total_cost,
        "department": "",
        "orderLines": _lines(totals),
    }


def main() -> int:
    tokens = {email: sign_in(email) for email in {ACME, WALMART}}
    created: list[tuple[str, str]] = []
    rows = []
    try:
        for name, user, totals, total_cost, update_totals, expected in CASES:
            token = tokens[user]
            payload = _fields(name, totals, total_cost) | {"titleShortDescription": name}
            request = _call("POST", "/requests", token, payload)
            created.append((request["id"], token))
            if update_totals is not None:
                update = _fields(name, update_totals, None) | {"title": name}
                request = _call("PUT", f"/requests/{request['id']}", token, update)
            rows.append((name, expected, request["approval_status"], request["total_cost"],
                         request["approved_by"], request["approved_at"]))
    finally:
        for request_id, token in created:
            try:
                _call("DELETE", f"/requests/{request_id}", token)
            except RuntimeError as exc:
                print(f"Cleanup failed for {request_id}: {exc}")

    header = ("case", "expected", "status", "stored total", "approved_by", "approved_at", "result")
    table = [(name, expected, status, f"{total:.2f}", str(by), "set" if at else "-",
              "PASS" if status == expected else "FAIL")
             for name, expected, status, total, by, at in rows]
    widths = [max(len(r[i]) for r in [header, *table]) for i in range(len(header))]
    for row in [header, *table]:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))
    passed = sum(row[-1] == "PASS" for row in table)
    print(f"\nPassed: {passed} of {len(CASES)}")
    print(f"Deleted {len(created)} requests created by this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
