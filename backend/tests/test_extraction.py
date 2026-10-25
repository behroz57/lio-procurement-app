"""Extraction post-processing with a mocked model response (no network, no DB).

Run with: python -m pytest tests/ -q
"""
import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models import CommodityGroup
from app.services import extraction


class _FakeSession:
    def scalars(self, _statement):
        return [CommodityGroup(id=31, category="Information Technology", name="Software")]


def _fake_openai(payload: dict):
    message = SimpleNamespace(content=json.dumps(payload), refusal=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )

    return FakeOpenAI


def _line(description, amount, unit_price, total, optional=False):
    return {
        "positionDescription": description,
        "amount": amount,
        "unit": "pieces",
        "unitPrice": unit_price,
        "totalPrice": total,
        "isOptional": optional,
    }


def _payload(**overrides):
    payload = {
        "title": "Office Chairs",
        "vendorName": "Example Supplies LLC",
        "customerName": "Buyer Co",
        "vatId": "12-3456789",
        "vatIdLabel": "EIN",
        "department": "",
        "orderLines": [_line("Chair", 2, 250.0, 500.0)],
        "documentGrandTotal": None,
        "documentTaxesAndFeesLines": [],
        "commodityGroupId": 31,
        "commodityGroupName": "Software",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def extract(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    def run(payload, required_fields=()):
        monkeypatch.setattr(extraction, "OpenAI", _fake_openai(payload))
        result = extraction.extract_vendor_data(
            "Quote text", _FakeSession(), list(required_fields)
        )
        assert result.success, result.error
        return result

    return run


def test_null_text_fields_become_empty(extract):
    line = _line(None, 1, 10.0, 10.0)
    line["unit"] = None
    result = extract(_payload(
        title=None, customerName=None, vatId=None, department=None, orderLines=[line]
    ))
    data = result.data
    assert (data.title, data.customer_name, data.vat_id, data.department) == ("", "", "", "")
    assert data.order_lines[0].position_description == ""
    assert data.order_lines[0].unit == ""


def test_string_numbers_are_parsed(extract):
    result = extract(_payload(orderLines=[
        _line("Laptop", "2", "$1,438.00", "2,876.00"),
        _line("Mystery item", "n/a", "call us", None),
    ]))
    laptop, mystery = result.data.order_lines
    assert (laptop.amount, laptop.unit_price, laptop.total_price) == (2.0, 1438.0, 2876.0)
    # Unparseable numbers become None for that field only.
    assert (mystery.amount, mystery.unit_price, mystery.total_price) == (None, None, None)
    assert any("Line 2" in w and "missing" in w for w in result.warnings)


def test_total_cost_is_computed_from_lines(extract):
    result = extract(_payload(
        totalCost=999.0,
        orderLines=[_line("A", 1, 0.1, 0.1), _line("B", 1, 0.2, 0.2), _line("C", 1, 10.0, 10.0)],
    ))
    assert result.data.total_cost == 10.3


def test_optional_lines_excluded_from_lines_and_total(extract):
    result = extract(_payload(orderLines=[
        _line("Panel", 1, 715.26, 715.26),
        _line("Panel, other color", 1, 430.0, 430.0, optional=True),
        _line("Shipping", 1, 215.0, 215.0),
    ]))
    data = result.data
    assert [l.position_description for l in data.order_lines] == ["Panel", "Shipping"]
    assert [l.position_description for l in data.optional_lines] == ["Panel, other color"]
    assert data.total_cost == 930.26


def test_reconciliation_warning_states_both_numbers(extract):
    result = extract(_payload(
        orderLines=[_line("A", 1, 100.0, 100.0), _line("B", 1, 50.0, 50.0)],
        documentGrandTotal=154.0,
        documentTaxesAndFeesLines=[14.0],
    ))
    warning = next(w for w in result.warnings if "grand total" in w)
    assert "150.00" in warning and "140.00" in warning
    assert "Removing line" not in warning


def test_reconciliation_names_the_one_line_that_closes_the_gap(extract):
    result = extract(_payload(
        orderLines=[_line("A", 1, 100.0, 100.0), _line("B", 1, 60.0, 60.0),
                    _line("C", 1, 20.0, 20.0)],
        documentGrandTotal=176.0,
        documentTaxesAndFeesLines=[16.0],
    ))
    warning = next(w for w in result.warnings if "grand total" in w)
    assert "Removing line 3 (C, 20.00)" in warning


def test_vendor_equal_to_customer_warns(extract):
    result = extract(_payload(vendorName="Buyer Co", customerName="buyer co"))
    assert any("same as the customer" in w for w in result.warnings)


def test_tax_id_with_a_non_tax_label_is_cleared(extract):
    result = extract(
        _payload(vatId="333-571207", vatIdLabel="Seller's Permit"), required_fields=["vat_id"]
    )
    assert result.data.vat_id == ""
    assert any('"333-571207"' in w and "Seller's Permit" in w for w in result.warnings)
    assert result.missing_fields == ["Tax ID"]

    result = extract(_payload(vatId="12-3456789", vatIdLabel="EIN"))
    assert result.data.vat_id == "12-3456789"
    assert not any("labeled" in w for w in result.warnings)


def test_empty_optional_fields_are_not_reported_missing(extract):
    result = extract(_payload(
        title="", vatId="", department="", commodityGroupId=None, commodityGroupName=None
    ))
    assert result.missing_fields is None


def test_core_fields_are_reported_missing(extract):
    result = extract(_payload(vendorName="", orderLines=[]))
    assert result.missing_fields == ["Vendor Name", "Order Lines"]


def test_org_required_fields_are_reported_missing(extract):
    result = extract(_payload(vatId="", department=""), required_fields=["vat_id", "department"])
    assert result.missing_fields == ["Tax ID", "Department"]

    result = extract(_payload(vatId="12-3456789", department=""), required_fields=["vat_id"])
    assert result.missing_fields is None


def test_taxes_are_summed_on_the_server(extract):
    result = extract(_payload(
        orderLines=[_line("Module", 13.78, 52.0, 716.56), _line("Edging", 15.75, 7.65, 120.49),
                    _line("Logo", 1, 350.0, 350.0), _line("Shipping", 1, 113.85, 113.85)],
        documentGrandTotal="$1,413.10",
        documentTaxesAndFeesLines=["$102.38", 9.82, "not a number"],
    ))
    assert result.data.document_taxes_and_fees_lines == [102.38, 9.82]
    assert result.data.total_cost == 1300.9
    assert result.warnings == []
