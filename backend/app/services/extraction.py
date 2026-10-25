import json
import logging

from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup
from app.schemas.extraction import ExtractedVendorData, ExtractionResponse
from app.services.commodity_rules import match_rule, rules_from_settings

logger = logging.getLogger(__name__)

# One retry when the reply can't be parsed.
MAX_ATTEMPTS = 2
LINE_TOLERANCE = 0.01
# Same seed on every call so repeated extractions of a quote vary less.
MODEL_SEED = 42
NON_TAX_ID_LABELS = ("permit", "account", "customer", "routing", "license")

API_ERROR_MESSAGE = (
    "The AI service could not process this document. "
    "Please try again or fill in the form manually."
)
PARSE_ERROR_MESSAGE = (
    "The AI response for this document could not be read. "
    "Please try again or fill in the form manually."
)

SYSTEM_PROMPT = (
    "You extract structured data from vendor quotes for a procurement team and "
    "classify each quote into a commodity group. Follow the rules exactly and only "
    "report what the document states."
)

_NULLABLE_NUMBER = {"type": ["number", "null"]}

# No totalCost: the server computes it from the lines, never the model.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "vendor_quote_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "vendorName": {"type": "string"},
                "customerName": {"type": "string"},
                "vatId": {"type": "string"},
                "vatIdLabel": {"type": "string"},
                "department": {"type": "string"},
                "orderLines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "positionDescription": {"type": "string"},
                            "amount": _NULLABLE_NUMBER,
                            "unit": {"type": "string"},
                            "unitPrice": _NULLABLE_NUMBER,
                            "totalPrice": _NULLABLE_NUMBER,
                            "isOptional": {"type": "boolean"},
                        },
                        "required": [
                            "positionDescription",
                            "amount",
                            "unit",
                            "unitPrice",
                            "totalPrice",
                            "isOptional",
                        ],
                        "additionalProperties": False,
                    },
                },
                "documentGrandTotal": _NULLABLE_NUMBER,
                "documentTaxesAndFeesLines": {"type": "array", "items": {"type": "number"}},
                "commodityGroupId": {"type": ["integer", "null"]},
                "commodityGroupName": {"type": ["string", "null"]},
            },
            "required": [
                "title",
                "vendorName",
                "customerName",
                "vatId",
                "vatIdLabel",
                "department",
                "orderLines",
                "documentGrandTotal",
                "documentTaxesAndFeesLines",
                "commodityGroupId",
                "commodityGroupName",
            ],
            "additionalProperties": False,
        },
    },
}


def _build_prompt(pdf_text: str, commodity_groups_list: str) -> str:
    return f"""Extract the procurement data from the vendor quote below and classify it into one commodity group.

FIELDS AND RULES

title: a short description (2-5 words) of what is being purchased.

vendorName: the company that issued and signs the quote. Look at the letterhead, the footer, the sender line above the address block, "Prepared by" / signature lines, the sales rep's email domain, and the company whose terms and conditions apply. Never use the addressee: the company in the recipient address block or after "Prepared for", "Bill to", "Ship to" or "Customer" is the buyer, not the vendor.

customerName: the addressee, i.e. the company the quote is addressed to (recipient address block, "Prepared for", "Bill to", "Customer"). The vendor and the customer are always different companies; if the name you picked for vendorName is the addressee, look again for the issuing company.

vatId: the vendor's tax ID (EIN, Federal Tax ID, TIN or VAT ID), often in the footer. Never the buyer's tax ID, a seller's permit, a license number or a bank number. Empty string if the document shows none.

vatIdLabel: the label printed next to that tax ID (e.g. "EIN", "Tax ID", "VAT ID"), exactly as printed. Empty string if there is no tax ID.

department: only if the document explicitly names a department of the buyer (for example "Department: Marketing"). Otherwise an empty string. Never put a company or person name here.

orderLines: every priced line of the quote.
- isOptional: true for any alternative, optional or "U.P." item, or an item priced without being added to the total. The marker ("Alt.", "Alternative", "Optional", "U.P." or similar) may be on the item's own row, on a separate line near the item, or in a preceding heading such as "Alternative: ...". false for items that are part of the order.
- Exclude heading lines that have no quantity and price, upsells, and marketing or descriptive text.
- The first number on a table row may be a position or item number, not the quantity. Use the value in the quantity column.
- unitPrice is the price per unit after any discount or surcharge, so amount × unitPrice = totalPrice.
- Keep fractional quantities exactly as written (e.g. 13.78) and keep the unit as written.
- Shipping, freight, delivery or transport charged as a cost is its own order line, even if it only appears in the totals block (amount 1, unitPrice = totalPrice = the charge).
- Sales tax, VAT and government fees (e.g. recycling or environmental fees) are not order lines.
- Prices are plain numbers without currency symbols or thousands separators ("1,438.00" -> 1438.00).
- Use null for a number the document does not state.

documentGrandTotal: the final total printed on the document (e.g. "Grand total", "Total due"), including taxes and fees. Null if none is printed.

documentTaxesAndFeesLines: every tax and government fee amount printed on the document (e.g. sales tax, VAT, eWaste or recycling fees), one entry per printed amount, exactly as printed. Do not add them up. Do not include subtotals, shipping or the grand total. Empty list if none are printed.

commodityGroupId / commodityGroupName: the single most appropriate group from the list below, based on the primary nature and purpose of the items. Use null for both if genuinely uncertain.

AVAILABLE COMMODITY GROUPS (ID | Category | Name):
{commodity_groups_list}

DOCUMENT TEXT:
<<<
{pdf_text}
>>>"""


class _UnreadableReply(Exception):
    pass


def _request_extraction(client: OpenAI, prompt: str) -> ExtractedVendorData:
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format=RESPONSE_FORMAT,
        temperature=0,
        seed=MODEL_SEED,
        max_completion_tokens=4000,
    )
    choice = response.choices[0]
    if not choice.message.content:
        refusal = getattr(choice.message, "refusal", None)
        raise _UnreadableReply(
            f"empty content (finish_reason={choice.finish_reason}, refusal={refusal!r})"
        )
    try:
        return ExtractedVendorData.model_validate(json.loads(choice.message.content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise _UnreadableReply(f"finish_reason={choice.finish_reason}: {exc}") from exc


def _reject_mislabeled_tax_id(extracted: ExtractedVendorData) -> list[str]:
    label = extracted.vat_id_label.casefold()
    if not extracted.vat_id or not any(word in label for word in NON_TAX_ID_LABELS):
        return []
    rejected = extracted.vat_id
    extracted.vat_id = ""
    return [
        f'The tax ID "{rejected}" is labeled "{extracted.vat_id_label}", which is not a '
        "tax ID, so it was cleared. Please enter the vendor's tax ID manually."
    ]


def _consistency_warnings(extracted: ExtractedVendorData) -> list[str]:
    warnings: list[str] = []
    vendor = extracted.vendor_name.strip()
    if not vendor:
        warnings.append("No vendor was found in the document.")
    elif vendor.casefold() == extracted.customer_name.strip().casefold():
        warnings.append(
            f"The vendor ({vendor}) is the same as the customer the quote is addressed to; "
            "please check the vendor name."
        )
    if not extracted.order_lines:
        warnings.append("No order lines were found in the document.")
    for index, line in enumerate(extracted.order_lines, start=1):
        label = f"Line {index} ({line.position_description or 'no description'})"
        if None in (line.amount, line.unit_price, line.total_price):
            warnings.append(f"{label}: quantity, unit price or total is missing.")
            continue
        expected = line.amount * line.unit_price
        if abs(expected - line.total_price) > LINE_TOLERANCE + 1e-9:
            warnings.append(
                f"{label}: quantity × unit price is {expected:.2f}, "
                f"but the line total is {line.total_price:.2f}."
            )

    # The lines should equal the printed grand total minus printed taxes and fees.
    grand_total = extracted.document_grand_total
    if grand_total is not None:
        document_net = grand_total - sum(extracted.document_taxes_and_fees_lines)
        line_sum = extracted.total_cost or 0
        if abs(line_sum - document_net) > LINE_TOLERANCE + 1e-9:
            message = (
                f"The order lines add up to {line_sum:.2f}, but the document's grand total "
                f"minus taxes and fees is {document_net:.2f}."
            )
            culprits = [
                (index, line)
                for index, line in enumerate(extracted.order_lines, start=1)
                if line.total_price is not None
                and abs(line_sum - line.total_price - document_net) <= LINE_TOLERANCE + 1e-9
            ]
            if len(culprits) == 1:
                index, line = culprits[0]
                message += (
                    f" Removing line {index} ({line.position_description or 'no description'}, "
                    f"{line.total_price:.2f}) would close the gap; check whether it belongs "
                    "in the order."
                )
            warnings.append(message)
    return warnings


def extract_vendor_data(pdf_text: str, db: Session, org_settings: dict) -> ExtractionResponse:
    """Extract vendor data and classify the commodity group in one AI call.

    ``org_settings`` is the caller's organization settings (required fields and
    commodity rules); empty for a user without an organization.
    """
    required_fields = org_settings.get("required_fields", [])
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    commodity_groups = list(
        db.scalars(select(CommodityGroup).order_by(CommodityGroup.id))
    )
    if not commodity_groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    # Give the model the full commodity catalog so it has complete context to
    # pick the most accurate match in a single pass.
    commodity_groups_list = "\n".join(
        f"{g.id} | {g.category} | {g.name}" for g in commodity_groups
    )
    prompt = _build_prompt(pdf_text, commodity_groups_list)

    client = OpenAI(api_key=settings.openai_api_key)
    extracted: ExtractedVendorData | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            extracted = _request_extraction(client, prompt)
            break
        except _UnreadableReply as exc:
            logger.warning("Unreadable extraction reply (attempt %d/%d): %s",
                           attempt, MAX_ATTEMPTS, exc)
        except Exception:  # noqa: BLE001 - log API errors, never show them to the user
            logger.exception("Extraction request to the AI service failed")
            return ExtractionResponse(success=False, error=API_ERROR_MESSAGE)
    if extracted is None:
        return ExtractionResponse(success=False, error=PARSE_ERROR_MESSAGE)

    # Before the missing-field check, so a cleared tax ID is reported if required.
    tax_id_warnings = _reject_mislabeled_tax_id(extracted)

    lines = extracted.order_lines
    extracted.order_lines = [line for line in lines if not line.is_optional]
    extracted.optional_lines = [line for line in lines if line.is_optional]

    # The total is always the sum of the extracted line totals, never the model's.
    extracted.total_cost = round(
        sum(line.total_price for line in extracted.order_lines if line.total_price is not None),
        2,
    )

    # Only core fields plus the org's required ones; quotes rarely name a department.
    missing_fields: list[str] = []
    if not extracted.vendor_name.strip():
        missing_fields.append("Vendor Name")
    if not extracted.order_lines:
        missing_fields.append("Order Lines")
    if "vat_id" in required_fields and not extracted.vat_id.strip():
        missing_fields.append("Tax ID")
    if "department" in required_fields and not extracted.department.strip():
        missing_fields.append("Department")

    # Validate that the returned commodity group id actually exists.
    if extracted.commodity_group_id is not None:
        valid_ids = {g.id for g in commodity_groups}
        if extracted.commodity_group_id not in valid_ids:
            logger.warning(
                "Invalid commodity group ID returned: %s", extracted.commodity_group_id
            )
            extracted.commodity_group_id = None
            extracted.commodity_group_name = None

    # Same rules as on request save, so the form shows the final group right away.
    match = match_rule(
        rules_from_settings(org_settings),
        [extracted.title, *(line.position_description for line in extracted.order_lines)],
        extracted.total_cost,
        extracted.commodity_group_id,
    )
    group = match and next(
        (g for g in commodity_groups if g.id == match[1].commodity_group_id), None
    )
    if group is not None:
        extracted.commodity_group_id = group.id
        extracted.commodity_group_name = group.name

    return ExtractionResponse(
        success=True,
        data=extracted,
        missing_fields=missing_fields or None,
        warnings=tax_id_warnings + _consistency_warnings(extracted),
    )
