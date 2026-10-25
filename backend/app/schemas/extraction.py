import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PdfParseResponse(BaseModel):
    text: str


class ExtractionRequest(BaseModel):
    text: str


def _to_text(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _to_number(value: object) -> float | None:
    # bool is a subclass of int; True must not become 1.0.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[\s$,]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_int(value: object) -> int | None:
    number = _to_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


# Lenient validators: one bad field from the AI never fails the whole extraction.
class OrderLineData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position_description: str = Field(default="", alias="positionDescription")
    unit_price: float | None = Field(default=None, alias="unitPrice")
    amount: float | None = None
    unit: str = ""
    total_price: float | None = Field(default=None, alias="totalPrice")
    is_optional: bool = Field(default=False, alias="isOptional")

    coerce_text = field_validator("position_description", "unit", mode="before")(_to_text)
    coerce_numbers = field_validator("unit_price", "amount", "total_price", mode="before")(
        _to_number
    )

    @field_validator("is_optional", mode="before")
    @classmethod
    def coerce_optional(cls, value: object) -> bool:
        return value is True or (isinstance(value, str) and value.strip().lower() == "true")


class ExtractedVendorData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    vendor_name: str = Field(default="", alias="vendorName")
    # The addressee ("Prepared for"); used to check it wasn't mistaken for the vendor.
    customer_name: str = Field(default="", alias="customerName")
    vat_id: str = Field(default="", alias="vatId")
    vat_id_label: str = Field(default="", alias="vatIdLabel")
    department: str = ""
    order_lines: list[OrderLineData] = Field(default_factory=list, alias="orderLines")
    # Alternatives and optional items, split out of order_lines on the server.
    optional_lines: list[OrderLineData] = Field(default_factory=list, alias="optionalLines")
    # Computed on the server from the order lines, never taken from the model.
    total_cost: float | None = Field(default=None, alias="totalCost")
    # As printed on the document; only used to reconcile against the line sum.
    document_grand_total: float | None = Field(default=None, alias="documentGrandTotal")
    # Each printed tax or fee amount; summed on the server because the model miscounts.
    document_taxes_and_fees_lines: list[float] = Field(
        default_factory=list, alias="documentTaxesAndFeesLines"
    )
    commodity_group_id: int | None = Field(default=None, alias="commodityGroupId")
    commodity_group_name: str | None = Field(default=None, alias="commodityGroupName")

    coerce_text = field_validator(
        "title", "vendor_name", "customer_name", "vat_id", "vat_id_label", "department",
        mode="before",
    )(_to_text)
    coerce_numbers = field_validator(
        "total_cost", "document_grand_total", mode="before"
    )(_to_number)
    coerce_group_id = field_validator("commodity_group_id", mode="before")(_to_int)

    @field_validator("document_taxes_and_fees_lines", mode="before")
    @classmethod
    def coerce_amounts(cls, value: object) -> list[float]:
        if not isinstance(value, list):
            return []
        return [n for n in map(_to_number, value) if n is not None]

    @field_validator("commodity_group_name", mode="before")
    @classmethod
    def coerce_group_name(cls, value: object) -> str | None:
        return None if value is None else _to_text(value)

    @field_validator("order_lines", "optional_lines", mode="before")
    @classmethod
    def coerce_lines(cls, value: object) -> list:
        # Drop anything that isn't a line object rather than failing the whole result.
        if not isinstance(value, list):
            return []
        return [line for line in value if isinstance(line, (dict, OrderLineData))]


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: ExtractedVendorData | None = None
    error: str | None = None
    missing_fields: list[str] | None = Field(default=None, alias="missingFields")
    warnings: list[str] = Field(default_factory=list)
