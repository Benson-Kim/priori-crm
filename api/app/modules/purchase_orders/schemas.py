"""
Pydantic schemas for the Purchase Orders API.

PO-02 scope: the line-item input used by the calculation engine and the
calculation-preview response. Request/response schemas for full CRUD
(create/update/get/list) are added in PO-03 / PO-04.

Financial contract (no discount in v1, PRD §9):
    line_total = quantity × unit_price
    tax_amount = line_total × tax_rate  (tax_rate from tax_type via get_tax_rate)
    subtotal   = Σ line_total
    tax_total  = Σ tax_amount
    total      = subtotal + tax_total

Mirrors the Expenses module (vendor-facing analog). "Rate"/"Amount" are
UI/PDF labels only; the wire/DB contract stays unit_price/line_total.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.constants.enums import TaxType

# LINE ITEM SCHEMAS


class PurchaseOrderLineItemCreate(BaseModel):
    """Payload for a single PO line item — used on create and full line-item
    replace, and as the input to the calculation-preview endpoint.

    Mirrors ExpenseLineItemCreate field-for-field so totals are guaranteed
    identical across the two vendor-facing document types.
    """

    item_name: str = Field(
        ...,
        min_length=1,
        max_length=500,
        alias="itemName",
        description="Product or service name",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Detailed description of the line item",
    )
    quantity: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Quantity — must be greater than zero",
    )
    unit_price: Decimal = Field(
        ...,
        ge=0,
        decimal_places=2,
        alias="unitPrice",
        description="Price per unit — zero allowed for zero-rated items",
    )
    tax_type: TaxType = Field(
        default=TaxType.VAT_16,
        alias="taxType",
        description="Tax type applied to this line item",
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "itemName": "Steel bolts M8",
                "description": "Box of 500 grade-8.8 bolts",
                "quantity": 10,
                "unitPrice": 1200.00,
                "taxType": "vat_16",
            }
        },
    }


# CALCULATION PREVIEW


class PurchaseOrderCalculationResponse(BaseModel):
    """
    Totals preview — POST /purchase-orders/calculate.

    No discount in v1: total = subtotal + tax_total. The field is named
    ``total`` (not ``total_due``) because a purchase order is a commitment
    to pay a vendor, not a receivable with a running balance — this matches
    the PurchaseOrder.total column added in PO-01.
    """

    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    line_items: list[dict] = Field(
        default_factory=list,
        description="Line items with calculated totals",
    )
