"""Score PDF extraction against the vendor-quote fixtures.

Runs every PDF in tests/fixtures/quotes through the live API (POST /pdf/parse,
then POST /extraction) and compares the result to expected.json. Standard
library only, so it runs without installing the backend.

Usage (backend must be running):
    python scripts/eval_extraction.py
    python scripts/eval_extraction.py --rescore   # re-score saved responses, no API calls

Writes the parsed text and full extraction response for each fixture to
out/eval/<file>.txt and out/eval/<file>.json (relative to backend/).
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_URL = "http://localhost:7200"
EMAIL = "requester@acme.com"
PASSWORD = "demo1234"

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures" / "quotes"
OUT_DIR = BACKEND_DIR / "out" / "eval"

FORBIDDEN_VENDOR = "lio technologies"
QTY_TOL = 0.001
MONEY_TOL = 0.01


class ApiError(Exception):
    pass


def _request(method: str, path: str, *, token: str | None = None,
             body: bytes | None = None, content_type: str | None = None,
             timeout: float = 180) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ApiError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"{method} {path} -> {exc.reason}") from None


def post_json(path: str, payload: dict, token: str | None = None) -> dict:
    return _request("POST", path, token=token, body=json.dumps(payload).encode("utf-8"),
                    content_type="application/json")


def post_pdf(path: str, filename: str, data: bytes, token: str) -> dict:
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/pdf\r\n\r\n",
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return _request("POST", path, token=token, body=body,
                    content_type=f"multipart/form-data; boundary={boundary}")


def sign_in() -> str:
    return post_json("/auth/signin", {"email": EMAIL, "password": PASSWORD})["access_token"]


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _close(a, b: float, tol: float) -> bool:
    a = _num(a)
    return a is not None and abs(a - b) <= tol + 1e-9


def _digits(value) -> str:
    return re.sub(r"\D", "", value or "")


def score(expected: dict, response: dict) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    ok = bool(response.get("success")) and isinstance(response.get("data"), dict)
    rows.append(("extraction succeeded", ok,
                 "" if ok else f"error: {response.get('error')!r}"))
    data = response.get("data") if ok else {}
    data = data or {}

    vendor = (data.get("vendorName") or "").strip()
    accepted = expected["vendor"]
    vendor_ok = (
        bool(vendor)
        and any(name.lower() in vendor.lower() for name in accepted)
        and FORBIDDEN_VENDOR not in vendor.lower()
    )
    rows.append(("vendor", vendor_ok, f"got {vendor!r}, accept {accepted}"))

    got_tax = data.get("vatId") or ""
    exp_tax = expected["tax_id"]
    tax_ok = (_digits(got_tax) == "") if exp_tax is None else (
        _digits(got_tax) == _digits(exp_tax)
    )
    rows.append(("tax ID", tax_ok, f"got {got_tax!r}, expected {exp_tax!r}"))

    dept = (data.get("department") or "").strip()
    rows.append(("department empty", dept == "", f"got {dept!r}"))

    lines = data.get("orderLines") or []
    exp_lines = expected["lines"]
    rows.append(("line count", len(lines) == len(exp_lines),
                 f"got {len(lines)}, expected {len(exp_lines)}"))

    unused = list(range(len(lines)))
    for i, exp in enumerate(exp_lines, 1):
        match = next(
            (j for j in unused if _close(lines[j].get("totalPrice"), exp["total"], MONEY_TOL)),
            None,
        )
        label = f"line {i} ({exp['hint']})"
        if match is None:
            for field in ("qty", "unit price", "total"):
                rows.append((f"{label} {field}", False, f"no line with total {exp['total']:.2f}"))
            continue
        unused.remove(match)
        got = lines[match]
        rows.append((f"{label} qty", _close(got.get("amount"), exp["qty"], QTY_TOL),
                     f"got {got.get('amount')}, expected {exp['qty']}"))
        rows.append((f"{label} unit price", _close(got.get("unitPrice"), exp["unit_price"], MONEY_TOL),
                     f"got {got.get('unitPrice')}, expected {exp['unit_price']:.2f}"))
        rows.append((f"{label} total", _close(got.get("totalPrice"), exp["total"], MONEY_TOL),
                     f"got {got.get('totalPrice')}, expected {exp['total']:.2f}"))

    rows.append(("total cost", _close(data.get("totalCost"), expected["total_cost"], MONEY_TOL),
                 f"got {data.get('totalCost')}, expected {expected['total_cost']:.2f}"))

    # Empty fields from a failed extraction must not earn points.
    if not ok:
        rows = [rows[0]] + [(check, False, "extraction failed") for check, _, _ in rows[1:]]
    return rows


def _pct(passed: int, total: int) -> str:
    return f"{100 * passed / total:.1f}%" if total else "n/a"


def print_table(name: str, rows: list[tuple[str, bool, str]]) -> None:
    width = max(len(r[0]) for r in rows)
    passed = sum(1 for r in rows if r[1])
    print(f"\n=== {name}  {passed}/{len(rows)} ({_pct(passed, len(rows))})")
    for check, ok, detail in rows:
        print(f"  {check.ljust(width)}  {'PASS' if ok else 'FAIL'}  {detail}")


def extract_live(name: str, token: str) -> tuple[dict, float | None]:
    seconds = None
    try:
        pdf_bytes = (FIXTURES_DIR / name).read_bytes()
        text = post_pdf("/pdf/parse", name, pdf_bytes, token)["text"]
        (OUT_DIR / f"{name}.txt").write_text(text, encoding="utf-8")
        started = time.perf_counter()
        response = post_json("/extraction", {"text": text}, token)
        seconds = time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001 - a crash must not stop the run
        response = {"crash": f"CRASH {type(exc).__name__}: {exc}"}
    (OUT_DIR / f"{name}.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
    return response, seconds


def load_saved(name: str) -> dict:
    path = OUT_DIR / f"{name}.json"
    if not path.exists():
        return {"crash": f"no saved response at {path}"}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rescore", action="store_true",
        help="score the responses saved in out/eval/ without calling the API",
    )
    args = parser.parse_args()

    expected_all = json.loads((FIXTURES_DIR / "expected.json").read_text(encoding="utf-8"))
    fixtures = sorted(k for k in expected_all if not k.startswith("_"))

    if args.rescore:
        print(f"Rescoring saved responses in {OUT_DIR}")
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            token = sign_in()
        except ApiError as exc:
            print(f"Sign-in failed: {exc}")
            return 1
        print(f"Signed in as {EMAIL} at {BASE_URL}")

    total_passed = total_checks = failed = 0
    durations: list[float] = []
    for name in fixtures:
        if args.rescore:
            response, seconds = load_saved(name), None
        else:
            response, seconds = extract_live(name, token)
        if "crash" in response:
            response = {"success": False, "error": response["crash"]}
        rows = score(expected_all[name], response)
        print_table(name, rows)
        if seconds is not None:
            durations.append(seconds)
            print(f"  extraction took {seconds:.1f} s")
        failed += not rows[0][1]
        total_passed += sum(1 for r in rows if r[1])
        total_checks += len(rows)

    print(f"\nOverall score: {total_passed} of {total_checks} checks "
          f"({_pct(total_passed, total_checks)})")
    print(f"Failed extractions: {failed} of {len(fixtures)}")
    if durations:
        print(f"Average extraction duration: {sum(durations) / len(durations):.1f} s "
              f"over {len(durations)} calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
