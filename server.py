"""
Zuora MCP Server
Exposes Zuora REST API tools for Accounts, Subscriptions, Invoices, and Payments.

Authentication uses OAuth 2.0 Client Credentials flow:
  1. POST /oauth/token with client_id + client_secret to obtain an access_token.
  2. The token is cached in memory and refreshed automatically when it expires.

Environment variables:
  ZUORA_CLIENT_ID     - Your Zuora OAuth client ID (required)
  ZUORA_CLIENT_SECRET - Your Zuora OAuth client secret (required)
  ZUORA_BASE_URL      - Override base URL (default: https://rest.apisandbox.zuora.com)

Account retrieval:
  - zuora_get_account uses legacy REST v1: GET /v1/accounts/{key}
  - zuora_get_account_v2 uses Quickstart API v2: GET /v2/accounts/{account_id}
  - zuora_get_account_object_query uses Object Query: GET /object-query/accounts/{key}
"""

import asyncio
import json
import os
import time
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ZUORA_BASE_URL = os.environ.get("ZUORA_BASE_URL", "https://rest.apisandbox.zuora.com").rstrip("/")
ZUORA_CLIENT_ID = os.environ.get("ZUORA_CLIENT_ID", "")
ZUORA_CLIENT_SECRET = os.environ.get("ZUORA_CLIENT_SECRET", "")

server = Server("zuora-mcp")


# ---------------------------------------------------------------------------
# OAuth token cache
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
}

_TOKEN_REFRESH_BUFFER = 60  # refresh 60 s before expiry


async def _get_access_token() -> str:
    """Return a valid access token, fetching a new one if necessary."""
    now = time.monotonic()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    url = f"{ZUORA_BASE_URL}/oauth/token"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": ZUORA_CLIENT_ID,
                "client_secret": ZUORA_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    response.raise_for_status()
    payload = response.json()
    _token_cache["access_token"] = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))
    _token_cache["expires_at"] = now + expires_in - _TOKEN_REFRESH_BUFFER
    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _headers() -> dict[str, str]:
    token = await _get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _request(
    method: str,
    path: str,
    body: dict | None = None,
    params: dict | list | None = None,
) -> dict[str, Any]:
    url = f"{ZUORA_BASE_URL}{path}"
    headers = await _headers()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            json=body,
            params=params,
        )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    return {"status_code": response.status_code, "data": data}


def _ok(result: dict) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[types.Tool] = [
    # ------------------------------------------------------------------ Accounts
    types.Tool(
        name="zuora_get_account",
        description="Retrieve a Zuora account by account key (account number or ID).",
        inputSchema={
            "type": "object",
            "properties": {
                "account_key": {
                    "type": "string",
                    "description": "The account number or account ID.",
                }
            },
            "required": ["account_key"],
        },
    ),
    types.Tool(
        name="zuora_create_account",
        description="Create a new Zuora account. Pass account fields as a JSON body.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account name (required by Zuora)."},
                "currency": {"type": "string", "description": "Three-letter ISO currency code, e.g. USD."},
                "billToContact": {
                    "type": "object",
                    "description": "Bill-to contact object with fields like firstName, lastName, address1, city, state, country, zipCode.",
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Any additional Zuora account fields to include in the request body.",
                },
            },
            "required": ["name", "currency"],
        },
    ),
    types.Tool(
        name="zuora_update_account",
        description="Update an existing Zuora account by account key.",
        inputSchema={
            "type": "object",
            "properties": {
                "account_key": {
                    "type": "string",
                    "description": "The account number or account ID to update.",
                },
                "fields": {
                    "type": "object",
                    "description": "Key/value pairs of account fields to update.",
                },
            },
            "required": ["account_key", "fields"],
        },
    ),
    types.Tool(
        name="zuora_delete_account",
        description="Delete a Zuora account by account key.",
        inputSchema={
            "type": "object",
            "properties": {
                "account_key": {
                    "type": "string",
                    "description": "The account number or account ID to delete.",
                }
            },
            "required": ["account_key"],
        },
    ),
    types.Tool(
        name="zuora_get_account_v2",
        description=(
            "Retrieve an account via Zuora Quickstart API v2 (GET /v2/accounts/{account_id}). "
            "Typically use the Zuora account object ID (UUID); some tenants may accept account number."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID (object UUID) or account number, per your tenant.",
                },
                "query_params": {
                    "type": "object",
                    "description": (
                        "Optional query string parameters (e.g. fields[], expand[] per Zuora Quickstart docs)."
                    ),
                },
            },
            "required": ["account_id"],
        },
    ),
    types.Tool(
        name="zuora_get_account_object_query",
        description=(
            "Retrieve an account via Zuora Object Query API (GET /object-query/accounts/{key}). "
            "Key may be account number or account ID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "account_key": {
                    "type": "string",
                    "description": "Account number or account ID.",
                },
                "query_params": {
                    "type": "object",
                    "description": (
                        "Optional query parameters (filter[], fields[], expand[], pageSize, etc.)."
                    ),
                },
            },
            "required": ["account_key"],
        },
    ),
    # -------------------------------------------------------------- Subscriptions
    types.Tool(
        name="zuora_get_subscription",
        description="Retrieve a Zuora subscription by subscription key (subscription number or ID).",
        inputSchema={
            "type": "object",
            "properties": {
                "subscription_key": {
                    "type": "string",
                    "description": "The subscription number or subscription ID.",
                },
                "charge_detail": {
                    "type": "string",
                    "description": "Optional. Level of charge detail: 'last-segment', 'current-segment', 'all-segments', or 'specific-segment'.",
                },
            },
            "required": ["subscription_key"],
        },
    ),
    types.Tool(
        name="zuora_list_subscriptions_by_account",
        description="List all subscriptions for a given Zuora account.",
        inputSchema={
            "type": "object",
            "properties": {
                "account_key": {
                    "type": "string",
                    "description": "The account number or account ID.",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page (default 20, max 40).",
                },
            },
            "required": ["account_key"],
        },
    ),
    types.Tool(
        name="zuora_create_subscription",
        description="Create a new Zuora subscription. Requires account key and rate plan data.",
        inputSchema={
            "type": "object",
            "properties": {
                "accountKey": {
                    "type": "string",
                    "description": "Account number or ID to attach the subscription to.",
                },
                "contractEffectiveDate": {
                    "type": "string",
                    "description": "Contract effective date in YYYY-MM-DD format.",
                },
                "subscribeToRatePlans": {
                    "type": "array",
                    "description": "Array of rate plan objects (each with productRatePlanId).",
                    "items": {"type": "object"},
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Any additional Zuora subscription fields.",
                },
            },
            "required": ["accountKey", "contractEffectiveDate", "subscribeToRatePlans"],
        },
    ),
    types.Tool(
        name="zuora_update_subscription",
        description="Update a Zuora subscription (e.g. add/remove rate plans, change terms).",
        inputSchema={
            "type": "object",
            "properties": {
                "subscription_key": {
                    "type": "string",
                    "description": "The subscription number or subscription ID to update.",
                },
                "fields": {
                    "type": "object",
                    "description": "Key/value pairs of subscription fields to update.",
                },
            },
            "required": ["subscription_key", "fields"],
        },
    ),
    types.Tool(
        name="zuora_cancel_subscription",
        description="Cancel a Zuora subscription.",
        inputSchema={
            "type": "object",
            "properties": {
                "subscription_key": {
                    "type": "string",
                    "description": "The subscription number or subscription ID to cancel.",
                },
                "cancellationPolicy": {
                    "type": "string",
                    "description": "Cancellation policy: 'EndOfCurrentTerm', 'EndOfLastInvoicePeriod', or 'SpecificDate'.",
                },
                "cancellationEffectiveDate": {
                    "type": "string",
                    "description": "Required if cancellationPolicy is 'SpecificDate'. Format: YYYY-MM-DD.",
                },
                "invoiceCollect": {
                    "type": "boolean",
                    "description": "Whether to generate an invoice and collect payment at cancellation.",
                },
            },
            "required": ["subscription_key", "cancellationPolicy"],
        },
    ),
    # ------------------------------------------------------------------ Invoices
    types.Tool(
        name="zuora_get_invoice",
        description="Retrieve a Zuora invoice by invoice ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "The invoice ID.",
                }
            },
            "required": ["invoice_id"],
        },
    ),
    types.Tool(
        name="zuora_list_invoices",
        description=(
            "List Zuora invoices using the Object Query API (GET /object-query/invoices). "
            "Supports filtering by account, status, date range, and cursor-based pagination."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Filter by account ID (UUID).",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by invoice status: 'Draft', 'Posted', or 'Split'.",
                    "enum": ["Draft", "Posted", "Split"],
                },
                "invoice_date_from": {
                    "type": "string",
                    "description": "Filter invoices with invoiceDate >= this date (YYYY-MM-DD).",
                },
                "invoice_date_to": {
                    "type": "string",
                    "description": "Filter invoices with invoiceDate <= this date (YYYY-MM-DD).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page (1–99, default 20).",
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor from a previous response's nextPage field.",
                },
                "sort": {
                    "type": "string",
                    "description": "Sort expression, e.g. 'invoicedate.DESC' or 'amount.ASC'.",
                },
                "expand": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Related objects to expand inline, e.g. ['account', 'invoiceitems'].",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict returned fields, e.g. ['id', 'invoicenumber', 'status', 'amount'].",
                },
            },
        },
    ),
    types.Tool(
        name="zuora_create_invoice",
        description="Create a standalone invoice for a Zuora account.",
        inputSchema={
            "type": "object",
            "properties": {
                "accountId": {
                    "type": "string",
                    "description": "The account ID to invoice.",
                },
                "invoiceDate": {
                    "type": "string",
                    "description": "Invoice date in YYYY-MM-DD format.",
                },
                "invoiceItems": {
                    "type": "array",
                    "description": "Array of invoice item objects.",
                    "items": {"type": "object"},
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Any additional invoice fields.",
                },
            },
            "required": ["accountId", "invoiceDate"],
        },
    ),
    types.Tool(
        name="zuora_update_invoice",
        description="Update an existing Zuora invoice by invoice ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "The invoice ID to update.",
                },
                "fields": {
                    "type": "object",
                    "description": "Key/value pairs of invoice fields to update.",
                },
            },
            "required": ["invoice_id", "fields"],
        },
    ),
    types.Tool(
        name="zuora_delete_invoice",
        description="Delete a Zuora invoice by invoice ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "The invoice ID to delete.",
                }
            },
            "required": ["invoice_id"],
        },
    ),
    # ------------------------------------------------------------ Credit Memos
    types.Tool(
        name="zuora_get_credit_memo",
        description="Retrieve a Zuora credit memo by credit memo ID or number (requires Invoice Settlement).",
        inputSchema={
            "type": "object",
            "properties": {
                "credit_memo_key": {
                    "type": "string",
                    "description": "The credit memo ID (UUID) or credit memo number (e.g. CM00000001).",
                }
            },
            "required": ["credit_memo_key"],
        },
    ),
    types.Tool(
        name="zuora_list_credit_memos",
        description=(
            "List Zuora credit memos with optional filters (requires Invoice Settlement). "
            "Uses GET /v1/credit-memos."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Filter by account ID."},
                "account_number": {"type": "string", "description": "Filter by account number."},
                "status": {
                    "type": "string",
                    "description": "Filter by status.",
                    "enum": ["Draft", "Posted", "Canceled", "Error", "PendingForTax", "Generating", "CancelInProgress"],
                },
                "referred_invoice_id": {"type": "string", "description": "Filter by originating invoice ID."},
                "credit_memo_date": {"type": "string", "description": "Filter by memo date (YYYY-MM-DD)."},
                "currency": {"type": "string", "description": "Filter by currency code, e.g. USD."},
                "number": {"type": "string", "description": "Filter by credit memo number."},
                "sort": {
                    "type": "string",
                    "description": "Sort expression, e.g. '+number' or '-amount'. Prefix with + (asc) or - (desc).",
                },
                "page": {"type": "integer", "description": "Page number (starts at 1, default 1)."},
                "page_size": {"type": "integer", "description": "Results per page (max 40, default 20)."},
            },
        },
    ),
    # ------------------------------------------------------------------ Payments
    types.Tool(
        name="zuora_get_payment",
        description="Retrieve a Zuora payment by payment ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "payment_id": {
                    "type": "string",
                    "description": "The payment ID.",
                }
            },
            "required": ["payment_id"],
        },
    ),
    types.Tool(
        name="zuora_list_payments",
        description="Query Zuora payments with optional filters.",
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Filter by account ID.",
                },
                "page": {"type": "integer", "description": "Page number (starts at 1)."},
                "page_size": {"type": "integer", "description": "Results per page (max 40)."},
            },
        },
    ),
    types.Tool(
        name="zuora_create_payment",
        description="Create a payment in Zuora.",
        inputSchema={
            "type": "object",
            "properties": {
                "accountId": {
                    "type": "string",
                    "description": "The account ID the payment belongs to.",
                },
                "amount": {
                    "type": "number",
                    "description": "Payment amount.",
                },
                "currency": {
                    "type": "string",
                    "description": "Three-letter ISO currency code, e.g. USD.",
                },
                "type": {
                    "type": "string",
                    "description": "Payment type: 'External' or 'Electronic'.",
                },
                "paymentMethodId": {
                    "type": "string",
                    "description": "ID of the payment method to charge.",
                },
                "invoices": {
                    "type": "array",
                    "description": "Array of invoice objects to apply the payment to (each with invoiceId and amount).",
                    "items": {"type": "object"},
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Any additional payment fields.",
                },
            },
            "required": ["accountId", "amount", "currency", "type"],
        },
    ),
    types.Tool(
        name="zuora_update_payment",
        description="Update an existing Zuora payment by payment ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "payment_id": {
                    "type": "string",
                    "description": "The payment ID to update.",
                },
                "fields": {
                    "type": "object",
                    "description": "Key/value pairs of payment fields to update.",
                },
            },
            "required": ["payment_id", "fields"],
        },
    ),
    types.Tool(
        name="zuora_delete_payment",
        description="Delete a Zuora payment by payment ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "payment_id": {
                    "type": "string",
                    "description": "The payment ID to delete.",
                }
            },
            "required": ["payment_id"],
        },
    ),
    # ------------------------------------------------------------------ Refunds
    types.Tool(
        name="zuora_get_refund",
        description="Retrieve a Zuora refund by refund ID or number (requires Invoice Settlement).",
        inputSchema={
            "type": "object",
            "properties": {
                "refund_key": {
                    "type": "string",
                    "description": "The refund ID (UUID) or refund number.",
                }
            },
            "required": ["refund_key"],
        },
    ),
    types.Tool(
        name="zuora_list_refunds",
        description=(
            "List Zuora refunds with optional filters (requires Invoice Settlement). "
            "Uses GET /v1/refunds."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Filter by account ID."},
                "status": {
                    "type": "string",
                    "description": "Filter by refund status.",
                    "enum": ["Processed", "Canceled", "Error", "Processing"],
                },
                "type": {
                    "type": "string",
                    "description": "Filter by refund type.",
                    "enum": ["External", "Electronic"],
                },
                "method_type": {
                    "type": "string",
                    "description": "Filter by payment method type.",
                    "enum": ["ACH", "Cash", "Check", "CreditCard", "PayPal", "WireTransfer", "DebitCard", "CreditCardReferenceTransaction", "BankTransfer", "Other"],
                },
                "payment_id": {"type": "string", "description": "Filter by associated payment ID."},
                "number": {"type": "string", "description": "Filter by refund number."},
                "refund_date": {"type": "string", "description": "Filter by refund date (YYYY-MM-DD)."},
                "sort": {
                    "type": "string",
                    "description": "Sort expression, e.g. '+number' or '-amount'. Prefix with + (asc) or - (desc).",
                },
                "page": {"type": "integer", "description": "Page number (starts at 1, default 1)."},
                "page_size": {"type": "integer", "description": "Results per page (max 40, default 20)."},
            },
        },
    ),
    # ------------------------------------------------ Custom Payment Method Types
    types.Tool(
        name="zuora_create_draft_payment_method_type",
        description=(
            "Create a draft custom payment method type in Zuora (POST /open-payment-method-types). "
            "The type starts in Draft status at revision 1. You must publish it before it goes live. "
            "Only usable with custom gateways set up through the Universal Payment Connector (UPC)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "internalName": {
                    "type": "string",
                    "description": (
                        "Alphanumeric string starting with a capital letter, no '_' or '-'. "
                        "Forms the API name as <internalName>__c_<tenantId>. Cannot be changed after creation. "
                        "Example: 'AmazonPay'."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": "Display label in the Zuora UI (alphanumeric, max 40 chars). Example: 'ZuoraQA Amazon Pay'.",
                },
                "tenantId": {
                    "type": "string",
                    "description": "Zuora tenant ID. Cannot be changed after creation. Example: '9'.",
                },
                "methodReferenceIdField": {
                    "type": "string",
                    "description": (
                        "Maps to a field name in the fields array; used as a filter in reporting tools. "
                        "Cannot be changed after creation. Example: 'AmazonToken'."
                    ),
                },
                "fields": {
                    "type": "array",
                    "description": (
                        "Array of field metadata objects (1–20 fields). "
                        "Each requires: name (alphanumeric, starts with capital letter), label, "
                        "type (string|date|datetime|number|boolean), index (unique int starting at 1), "
                        "checksum (bool), editable (bool), visible (bool), representer (bool — at least one must be true), required (bool). "
                        "Optional per field: description, defaultValue, maxLength (1–8000), minLength. "
                        "Most field properties cannot be changed after the type is created."
                    ),
                    "items": {"type": "object"},
                },
                "entityId": {
                    "type": "string",
                    "description": "Optional. UUID of a specific entity; if omitted, available to all entities in the tenant.",
                },
                "isSupportAsyncPayment": {
                    "type": "boolean",
                    "description": "Enable Asynchronous Payment Statuses feature. Default: false.",
                },
                "subTypeField": {
                    "type": "string",
                    "description": "Optional. Maps to a field name used for subtype filtering in reports.",
                },
                "userReferenceIdField": {
                    "type": "string",
                    "description": "Optional. Maps to a field name identifying the user or customer account.",
                },
            },
            "required": ["internalName", "label", "tenantId", "methodReferenceIdField", "fields"],
        },
    ),
    types.Tool(
        name="zuora_publish_payment_method_type",
        description=(
            "Publish a draft custom payment method type so it goes live "
            "(PUT /open-payment-method-types/publish/{paymentMethodTypeName}). "
            "After publishing, the status changes from Draft to Published."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "payment_method_type_name": {
                    "type": "string",
                    "description": "The API name of the custom payment method type, e.g. 'AmazonPay__c_12368'.",
                },
            },
            "required": ["payment_method_type_name"],
        },
    ),
    types.Tool(
        name="zuora_update_payment_method_type",
        description=(
            "Update a custom payment method type (PUT /open-payment-method-types/{paymentMethodTypeName}). "
            "Updates the latest draft; if already published, the revision number increments. "
            "You must publish again for changes to take effect. "
            "Note: name, type, index, checksum, required, editable, and defaultValue on each field "
            "cannot be changed after initial creation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "payment_method_type_name": {
                    "type": "string",
                    "description": "The API name of the custom payment method type, e.g. 'AmazonPay__c_12368'.",
                },
                "internalName": {
                    "type": "string",
                    "description": "Must match the original value; cannot be changed.",
                },
                "label": {
                    "type": "string",
                    "description": "Updated display label in the Zuora UI.",
                },
                "tenantId": {
                    "type": "string",
                    "description": "Must match the original value; cannot be changed.",
                },
                "methodReferenceIdField": {
                    "type": "string",
                    "description": "Must match the original value; cannot be changed.",
                },
                "fields": {
                    "type": "array",
                    "description": (
                        "Full fields array including all fields (changed and unchanged). "
                        "Mutable per-field properties: description, label, "
                        "maxLength (can only increase), minLength (can only decrease)."
                    ),
                    "items": {"type": "object"},
                },
                "entityId": {
                    "type": "string",
                    "description": "Can only be updated to empty string (removes entity restriction).",
                },
                "isSupportAsyncPayment": {"type": "boolean"},
                "subTypeField": {"type": "string"},
                "userReferenceIdField": {"type": "string"},
            },
            "required": ["payment_method_type_name", "internalName", "tenantId", "methodReferenceIdField", "fields"],
        },
    ),
    # ------------------------------------------------------- Payment Methods
    types.Tool(
        name="zuora_create_payment_method",
        description=(
            "Create a payment method in Zuora (POST /v1/payment-methods). "
            "Supports 18 types: CreditCard, CreditCardReferenceTransaction, ACH, SEPA, "
            "Betalingsservice, Autogiro, Bacs, Becs, Becsnz, PAD, PayPalCP, PayPalEC, "
            "PayPalNativeEC, PayPalAdaptive, AdyenApplePay, AdyenGooglePay, GooglePay, AmazonPay. "
            "Omit accountKey to create an orphan payment method (associate later via update)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Payment method type.",
                    "enum": [
                        "CreditCard", "CreditCardReferenceTransaction", "ACH", "SEPA",
                        "Betalingsservice", "Autogiro", "Bacs", "Becs", "Becsnz", "PAD",
                        "PayPalCP", "PayPalEC", "PayPalNativeEC", "PayPalAdaptive",
                        "AdyenApplePay", "AdyenGooglePay", "GooglePay", "AmazonPay",
                    ],
                },
                "accountKey": {
                    "type": "string",
                    "description": "Account ID (UUID) or account number. Omit to create an orphan payment method.",
                },
                "authGateway": {
                    "type": "string",
                    "description": "Internal ID of the payment gateway to use for authorization. Defaults to the account's or tenant's default gateway.",
                },
                "cardHolderInfo": {
                    "type": "object",
                    "description": (
                        "Required for CreditCard. Container for cardholder info. "
                        "Required nested field: cardHolderName (max 50 chars, US-ASCII). "
                        "Optional: addressLine1, addressLine2, city, state, zipCode, country, email, phone."
                    ),
                },
                "cardNumber": {
                    "type": "string",
                    "description": "Required for CreditCard. Full card number, e.g. '4111111111111111'.",
                },
                "cardType": {
                    "type": "string",
                    "description": "Required for CreditCard.",
                    "enum": ["Visa", "MasterCard", "AmericanExpress", "Discover", "JCB", "Diners"],
                },
                "expirationMonth": {
                    "type": "integer",
                    "description": "Required for CreditCard. Expiration month (1–12).",
                },
                "expirationYear": {
                    "type": "integer",
                    "description": "Required for CreditCard. Four-digit expiration year, e.g. 2028.",
                },
                "securityCode": {
                    "type": "string",
                    "description": "CVV/CVV2 code. Not stored or queryable (PCI compliance). Optional even for CreditCard.",
                },
                "mandateInfo": {
                    "type": "object",
                    "description": "Mandate information for ACH, SEPA, and other direct debit types.",
                },
                "mitProfileAction": {
                    "type": "string",
                    "description": (
                        "How to create the stored credential (MIT) profile. "
                        "'Activate' (default) — validates via CIT transaction. "
                        "'Persist' — profile already exists externally; requires mitNetworkTransactionId."
                    ),
                    "enum": ["Activate", "Persist"],
                },
                "mitProfileType": {
                    "type": "string",
                    "description": "Required if mitProfileAction is set. Defaults to 'Recurring'.",
                    "enum": ["Recurring", "Unscheduled"],
                },
                "mitConsentAgreementRef": {
                    "type": "string",
                    "description": "Reference for the stored credential consent agreement (max 128 chars).",
                },
                "mitConsentAgreementSrc": {
                    "type": "string",
                    "description": "Required if mitProfileAction is set. How consent was established. Allowed value: 'External'.",
                    "enum": ["External"],
                },
                "mitNetworkTransactionId": {
                    "type": "string",
                    "description": "Required if mitProfileAction is 'Persist'. Network transaction ID (max 128 chars).",
                },
                "mitProfileAgreedOn": {
                    "type": "string",
                    "description": "Date the stored credential profile was agreed to (YYYY-MM-DD).",
                },
                "processingOptions": {
                    "type": "object",
                    "description": "Processing options, e.g. {'checkDuplicated': true} to detect duplicate payment methods on the account.",
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Any additional Zuora payment method fields (e.g. type-specific fields for PayPal, ACH, SEPA, tokenization, etc.).",
                },
            },
            "required": ["type"],
        },
    ),
    types.Tool(
        name="zuora_get_payment_method",
        description=(
            "Retrieve detailed information about a payment method by ID "
            "(GET /v1/payment-methods/{payment-method-id}). "
            "Works for electronic payment methods: Credit Card, ACH, SEPA, PayPal, Apple Pay, "
            "Google Pay, and custom Open Payment Method types. "
            "To retrieve both electronic and non-electronic methods, use zuora_get_account_object_query instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "payment_method_id": {
                    "type": "string",
                    "description": "Unique ID of the payment method.",
                },
            },
            "required": ["payment_method_id"],
        },
    ),
    types.Tool(
        name="zuora_delete_payment_method",
        description=(
            "Delete a payment method by ID (DELETE /v1/payment-methods/{payment-method-id}). "
            "The payment method must not be the account's default — "
            "designate a different default first if needed, otherwise the request will fail."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "payment_method_id": {
                    "type": "string",
                    "description": "Unique ID of the payment method to delete.",
                },
            },
            "required": ["payment_method_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # ---------------------------------------------------------------- Accounts
    if name == "zuora_get_account":
        key = arguments["account_key"]
        return _ok(await _request("GET", f"/v1/accounts/{key}"))

    if name == "zuora_create_account":
        body: dict[str, Any] = {
            "name": arguments["name"],
            "currency": arguments["currency"],
        }
        if "billToContact" in arguments:
            body["billToContact"] = arguments["billToContact"]
        body.update(arguments.get("extra_fields") or {})
        return _ok(await _request("POST", "/v1/accounts", body=body))

    if name == "zuora_update_account":
        key = arguments["account_key"]
        return _ok(await _request("PUT", f"/v1/accounts/{key}", body=arguments["fields"]))

    if name == "zuora_delete_account":
        key = arguments["account_key"]
        return _ok(await _request("DELETE", f"/v1/accounts/{key}"))

    if name == "zuora_get_account_v2":
        aid = arguments["account_id"]
        q = arguments.get("query_params")
        return _ok(await _request("GET", f"/v2/accounts/{aid}", params=q))

    if name == "zuora_get_account_object_query":
        key = arguments["account_key"]
        q = arguments.get("query_params")
        return _ok(await _request("GET", f"/object-query/accounts/{key}", params=q))

    # ---------------------------------------------------------- Subscriptions
    if name == "zuora_get_subscription":
        key = arguments["subscription_key"]
        params = {}
        if "charge_detail" in arguments:
            params["charge-detail"] = arguments["charge_detail"]
        return _ok(await _request("GET", f"/v1/subscriptions/{key}", params=params or None))

    if name == "zuora_list_subscriptions_by_account":
        key = arguments["account_key"]
        params = {}
        if "page_size" in arguments:
            params["pageSize"] = arguments["page_size"]
        return _ok(await _request("GET", f"/v1/subscriptions/accounts/{key}", params=params or None))

    if name == "zuora_create_subscription":
        body = {
            "accountKey": arguments["accountKey"],
            "contractEffectiveDate": arguments["contractEffectiveDate"],
            "subscribeToRatePlans": arguments["subscribeToRatePlans"],
        }
        body.update(arguments.get("extra_fields") or {})
        return _ok(await _request("POST", "/v1/subscriptions", body=body))

    if name == "zuora_update_subscription":
        key = arguments["subscription_key"]
        return _ok(await _request("PUT", f"/v1/subscriptions/{key}", body=arguments["fields"]))

    if name == "zuora_cancel_subscription":
        key = arguments["subscription_key"]
        body = {"cancellationPolicy": arguments["cancellationPolicy"]}
        if "cancellationEffectiveDate" in arguments:
            body["cancellationEffectiveDate"] = arguments["cancellationEffectiveDate"]
        if "invoiceCollect" in arguments:
            body["invoiceCollect"] = arguments["invoiceCollect"]
        return _ok(await _request("PUT", f"/v1/subscriptions/{key}/cancel", body=body))

    # ---------------------------------------------------------------- Invoices
    if name == "zuora_get_invoice":
        inv_id = arguments["invoice_id"]
        return _ok(await _request("GET", f"/v1/invoices/{inv_id}"))

    if name == "zuora_list_invoices":
        # Object Query API uses repeated filter[] params — build as list of tuples
        params: list[tuple[str, Any]] = []
        if "account_id" in arguments:
            params.append(("filter[]", f"accountid.EQ:{arguments['account_id']}"))
        if "status" in arguments:
            params.append(("filter[]", f"status.EQ:{arguments['status']}"))
        if "invoice_date_from" in arguments:
            params.append(("filter[]", f"invoicedate.GTE:{arguments['invoice_date_from']}"))
        if "invoice_date_to" in arguments:
            params.append(("filter[]", f"invoicedate.LTE:{arguments['invoice_date_to']}"))
        if "sort" in arguments:
            params.append(("sort[]", arguments["sort"]))
        if "page_size" in arguments:
            params.append(("pageSize", arguments["page_size"]))
        if "cursor" in arguments:
            params.append(("cursor", arguments["cursor"]))
        for field in arguments.get("expand") or []:
            params.append(("expand[]", field))
        for field in arguments.get("fields") or []:
            params.append(("fields[]", field))
        return _ok(await _request("GET", "/object-query/invoices", params=params or None))

    if name == "zuora_create_invoice":
        body = {
            "accountId": arguments["accountId"],
            "invoiceDate": arguments["invoiceDate"],
        }
        if "invoiceItems" in arguments:
            body["invoiceItems"] = arguments["invoiceItems"]
        body.update(arguments.get("extra_fields") or {})
        return _ok(await _request("POST", "/v1/invoices", body=body))

    if name == "zuora_update_invoice":
        inv_id = arguments["invoice_id"]
        return _ok(await _request("PUT", f"/v1/invoices/{inv_id}", body=arguments["fields"]))

    if name == "zuora_delete_invoice":
        inv_id = arguments["invoice_id"]
        return _ok(await _request("DELETE", f"/v1/invoices/{inv_id}"))

    # ---------------------------------------------------------- Credit Memos
    if name == "zuora_get_credit_memo":
        key = arguments["credit_memo_key"]
        return _ok(await _request("GET", f"/v1/credit-memos/{key}"))

    if name == "zuora_list_credit_memos":
        params: dict[str, Any] = {}
        for arg, param in [
            ("account_id", "accountId"),
            ("account_number", "accountNumber"),
            ("status", "status"),
            ("referred_invoice_id", "referredInvoiceId"),
            ("credit_memo_date", "creditMemoDate"),
            ("currency", "currency"),
            ("number", "number"),
            ("sort", "sort"),
            ("page", "page"),
            ("page_size", "pageSize"),
        ]:
            if arg in arguments:
                params[param] = arguments[arg]
        return _ok(await _request("GET", "/v1/credit-memos", params=params or None))

    # ---------------------------------------------------------------- Payments
    if name == "zuora_get_payment":
        pay_id = arguments["payment_id"]
        return _ok(await _request("GET", f"/v1/payments/{pay_id}"))

    if name == "zuora_list_payments":
        params = {}
        if "account_id" in arguments:
            params["accountId"] = arguments["account_id"]
        if "page" in arguments:
            params["page"] = arguments["page"]
        if "page_size" in arguments:
            params["pageSize"] = arguments["page_size"]
        return _ok(await _request("GET", "/v1/payments", params=params or None))

    if name == "zuora_create_payment":
        body = {
            "accountId": arguments["accountId"],
            "amount": arguments["amount"],
            "currency": arguments["currency"],
            "type": arguments["type"],
        }
        for optional in ("paymentMethodId", "invoices"):
            if optional in arguments:
                body[optional] = arguments[optional]
        body.update(arguments.get("extra_fields") or {})
        return _ok(await _request("POST", "/v1/payments", body=body))

    if name == "zuora_update_payment":
        pay_id = arguments["payment_id"]
        return _ok(await _request("PUT", f"/v1/payments/{pay_id}", body=arguments["fields"]))

    if name == "zuora_delete_payment":
        pay_id = arguments["payment_id"]
        return _ok(await _request("DELETE", f"/v1/payments/{pay_id}"))

    # ---------------------------------------------------------------- Refunds
    if name == "zuora_get_refund":
        key = arguments["refund_key"]
        return _ok(await _request("GET", f"/v1/refunds/{key}"))

    if name == "zuora_list_refunds":
        params = {}
        for arg, param in [
            ("account_id", "accountId"),
            ("status", "status"),
            ("type", "type"),
            ("method_type", "methodType"),
            ("payment_id", "paymentId"),
            ("number", "number"),
            ("refund_date", "refundDate"),
            ("sort", "sort"),
            ("page", "page"),
            ("page_size", "pageSize"),
        ]:
            if arg in arguments:
                params[param] = arguments[arg]
        return _ok(await _request("GET", "/v1/refunds", params=params or None))

    # ---------------------------------------- Custom Payment Method Types
    if name == "zuora_create_draft_payment_method_type":
        body: dict[str, Any] = {
            "internalName": arguments["internalName"],
            "label": arguments["label"],
            "tenantId": arguments["tenantId"],
            "methodReferenceIdField": arguments["methodReferenceIdField"],
            "fields": arguments["fields"],
        }
        for optional in ("entityId", "isSupportAsyncPayment", "subTypeField", "userReferenceIdField"):
            if optional in arguments:
                body[optional] = arguments[optional]
        return _ok(await _request("POST", "/open-payment-method-types", body=body))

    if name == "zuora_publish_payment_method_type":
        type_name = arguments["payment_method_type_name"]
        return _ok(await _request("PUT", f"/open-payment-method-types/publish/{type_name}"))

    if name == "zuora_update_payment_method_type":
        type_name = arguments["payment_method_type_name"]
        body = {
            "internalName": arguments["internalName"],
            "tenantId": arguments["tenantId"],
            "methodReferenceIdField": arguments["methodReferenceIdField"],
            "fields": arguments["fields"],
        }
        for optional in ("label", "entityId", "isSupportAsyncPayment", "subTypeField", "userReferenceIdField"):
            if optional in arguments:
                body[optional] = arguments[optional]
        return _ok(await _request("PUT", f"/open-payment-method-types/{type_name}", body=body))

    # ---------------------------------------------------- Payment Methods
    if name == "zuora_create_payment_method":
        body: dict[str, Any] = {"type": arguments["type"]}
        for field in (
            "accountKey", "authGateway", "cardHolderInfo", "cardNumber", "cardType",
            "expirationMonth", "expirationYear", "securityCode", "mandateInfo",
            "mitProfileAction", "mitProfileType", "mitConsentAgreementRef",
            "mitConsentAgreementSrc", "mitNetworkTransactionId", "mitProfileAgreedOn",
            "processingOptions",
        ):
            if field in arguments:
                body[field] = arguments[field]
        body.update(arguments.get("extra_fields") or {})
        return _ok(await _request("POST", "/v1/payment-methods", body=body))

    if name == "zuora_get_payment_method":
        pm_id = arguments["payment_method_id"]
        return _ok(await _request("GET", f"/v1/payment-methods/{pm_id}"))

    if name == "zuora_delete_payment_method":
        pm_id = arguments["payment_method_id"]
        return _ok(await _request("DELETE", f"/v1/payment-methods/{pm_id}"))

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    import sys
    if not ZUORA_CLIENT_ID or not ZUORA_CLIENT_SECRET:
        print(
            "WARNING: ZUORA_CLIENT_ID and/or ZUORA_CLIENT_SECRET environment variables "
            "are not set. OAuth token requests will fail.",
            file=sys.stderr,
        )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
