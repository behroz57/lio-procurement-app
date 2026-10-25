"""Check organization commodity rules end to end against the live API.

For each case: (a) send a short quote text to /extraction and record the
suggested group, (b) create the request (submitting group 49 unless the case
says otherwise) and record the saved group and whether a commodity_rule
activity entry exists. Deletes every request it created. Standard library only.

Usage (backend must be running with an OpenAI key):
    python scripts/eval_categorisation.py
"""
import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://localhost:7200"
PASSWORD = "demo1234"
SUBMITTED_GROUP = 49

# expected: a group id, or None for "unchanged" (saved group stays the submitted one).
# extraction_not: for unchanged cases, a group the suggestion must not be; omitted means n/a.
CASES = [
    {"name": "1 cable ties", "user": "requester@acme.com", "expected": 11,
     "lines": [("Cable ties 200 pcs", 150.0)]},
    {"name": "2 printer toner", "user": "requester@acme.com", "expected": 29,
     "lines": [("Printer toner cartridges", 300.0)]},
    {"name": "3 small software", "user": "requester@acme.com", "expected": 15,
     "lines": [("Adobe Acrobat software subscription", 600.0)]},
    {"name": "4 large software", "user": "requester@acme.com", "expected": None,
     "extraction_not": 15, "lines": [("Software subscription annual", 2500.0)]},
    {"name": "5 office chair", "user": "requester@acme.com", "expected": None,
     "lines": [("Office chair", 400.0)]},
    {"name": "6 cable ties, Walmart", "user": "admin@walmart.com", "expected": None,
     "lines": [("Cable ties 200 pcs", 150.0)]},
    {"name": "7 toner + SaaS", "user": "requester@acme.com", "expected": 29,
     "lines": [("Printer toner cartridges", 200.0), ("SaaS subscription", 100.0)]},
    {"name": "8 cable ties, group 49", "user": "requester@acme.com", "expected": 11,
     "lines": [("Cable ties 200 pcs", 150.0)]},
]


class ApiError(Exception):
    pass


def _request(method: str, path: str, token: str | None = None, payload: dict | None = None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ApiError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None


def sign_in(email: str) -> str:
    return _request("POST", "/auth/signin", payload={"email": email, "password": PASSWORD})[
        "access_token"
    ]


def quote_text(case: dict, customer: str) -> str:
    rows = "\n".join(f"{i}  {desc}  1  {total:.2f}  {total:.2f}"
                     for i, (desc, total) in enumerate(case["lines"], start=1))
    total = sum(t for _, t in case["lines"])
    return (
        "Example Supplies LLC | 1 Main Street | Springfield, IL 62701\n"
        f"Quote Q-{case['name'].split()[0]}\nPrepared for: {customer}\n"
        "Pos  Description  Qty  Unit Price $  Total $\n"
        f"{rows}\nTotal ${total:.2f}\nEIN: 12-3456789\n"
    )


def request_payload(case: dict) -> dict:
    return {
        "requestorName": "Categorisation eval",
        "titleShortDescription": " and ".join(desc for desc, _ in case["lines"]),
        "vendorName": "Example Supplies LLC",
        "vatId": "12-3456789",
        "commodityGroupId": SUBMITTED_GROUP,
        "totalCost": sum(t for _, t in case["lines"]),
        "department": "",
        "orderLines": [
            {"positionDescription": desc, "unitPrice": total, "amount": 1,
             "unit": "piece", "totalPrice": total}
            for desc, total in case["lines"]
        ],
    }


def run_case(case: dict, token: str, created: list) -> dict:
    customer = "Walmart" if "walmart" in case["user"] else "Acme Corp"
    extraction = _request("POST", "/extraction", token, {"text": quote_text(case, customer)})
    suggested = (extraction.get("data") or {}).get("commodityGroupId") \
        if extraction.get("success") else f"error: {extraction.get('error')}"

    saved = _request("POST", "/requests", token, request_payload(case))
    created.append((saved["id"], token))
    activity = _request("GET", f"/requests/{saved['id']}/activity", token)
    has_rule_entry = any(a["action"] == "commodity_rule" for a in activity)

    expected = case["expected"]
    if expected is not None:
        extraction_ok = suggested == expected
    elif "extraction_not" in case:
        extraction_ok = isinstance(suggested, int) and suggested != case["extraction_not"]
    else:
        extraction_ok = None
    saved_expected = expected if expected is not None else SUBMITTED_GROUP
    saved_ok = saved["commodity_group_id"] == saved_expected and has_rule_entry == (
        expected is not None
    )
    return {"suggested": suggested, "saved": saved["commodity_group_id"],
            "activity": has_rule_entry, "extraction_ok": extraction_ok, "saved_ok": saved_ok}


def _mark(ok: bool | None) -> str:
    return "n/a" if ok is None else ("PASS" if ok else "FAIL")


def main() -> int:
    tokens: dict[str, str] = {}
    created: list[tuple[str, str]] = []
    results = []
    try:
        for case in CASES:
            if case["user"] not in tokens:
                tokens[case["user"]] = sign_in(case["user"])
            results.append((case, run_case(case, tokens[case["user"]], created)))
    except ApiError as exc:
        print(f"Aborted: {exc}")
        return 1
    finally:
        for request_id, token in created:
            try:
                _request("DELETE", f"/requests/{request_id}", token)
            except ApiError as exc:
                print(f"Cleanup failed for {request_id}: {exc}")

    header = ("case", "expected", "extraction", "saved", "activity", "extraction", "saved")
    rows = [(
        case["name"],
        str(case["expected"]) if case["expected"] is not None else f"unchanged ({SUBMITTED_GROUP})",
        str(r["suggested"]), str(r["saved"]), "yes" if r["activity"] else "no",
        _mark(r["extraction_ok"]), _mark(r["saved_ok"]),
    ) for case, r in results]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))

    marks = [r["extraction_ok"] for _, r in results]
    print(f"\nExtraction: {marks.count(True)} of {len(CASES)} passed "
          f"({marks.count(False)} failed, {marks.count(None)} n/a)")
    print(f"Saved: {sum(r['saved_ok'] for _, r in results)} of {len(CASES)} passed")
    print(f"Deleted {len(created)} requests created by this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
