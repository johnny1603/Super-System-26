import base64
import json
import os
from datetime import datetime

import httpx

from agents.keys_agent import get_key

BASE_URL = "https://api-m.sandbox.paypal.com"
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
TIMEOUT = 15


def _access_token() -> str:
    client_id = get_key("PAYPAL_CLIENT_ID")
    client_secret = get_key("PAYPAL_CLIENT_SECRET")
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    response = httpx.post(
        f"{BASE_URL}/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }


# ─── Subscriptions ────────────────────────────────────────────────────────────

def create_plan(product_id: str, plan_name: str, amount: float, currency: str = "ILS",
                 setup_fee: float = 0) -> str:
    """A monthly billing plan on an existing product. Used at checkout and again
    when a client upgrades (the upgraded plan must live under the SAME product
    as the original, or the subscription revision is rejected).

    setup_fee (checkout only, not upgrades) rides PayPal's native
    payment_preferences.setup_fee - charged upfront in the same approval flow
    as the first subscription payment. setup_fee_failure_action=CANCEL so we
    never end up servicing a client whose setup fee didn't actually collect.

    Billing timeline (per the pricing model quoted to the client in every
    proposal's honest_note - month 1 = setup fee only, month 2 = management
    fee free, month 3+ = full billing): a checkout plan (setup_fee > 0) gets
    TWO zero-price TRIAL cycles covering months 1-2 before the REGULAR cycle
    starts billing the real amount from month 3 onward. Without the trial
    cycles, PayPal captures the REGULAR cycle's first charge in the SAME
    approval as the setup fee, so the client would be charged setup fee +
    month 1 management fee together - not what's promised.

    An upgrade plan (setup_fee=0, see api_server.py's client_upgrade) must
    NOT repeat the two-free-months offer - it gets a single REGULAR cycle at
    full price, unchanged from before."""
    payment_preferences = {
        "auto_bill_outstanding": True,
        "payment_failure_threshold": 3,
    }
    if setup_fee:
        payment_preferences["setup_fee"] = {"value": str(setup_fee), "currency_code": currency}
        payment_preferences["setup_fee_failure_action"] = "CANCEL"

    billing_cycles = []
    next_sequence = 1
    if setup_fee:
        for _ in range(2):  # months 1-2: setup fee only / free management fee
            billing_cycles.append({
                "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                "tenure_type": "TRIAL",
                "sequence": next_sequence,
                "total_cycles": 1,
                "pricing_scheme": {
                    "fixed_price": {"value": "0", "currency_code": currency}
                },
            })
            next_sequence += 1

    billing_cycles.append({
        "frequency": {"interval_unit": "MONTH", "interval_count": 1},
        "tenure_type": "REGULAR",
        "sequence": next_sequence,
        "total_cycles": 0,  # infinite
        "pricing_scheme": {
            "fixed_price": {"value": str(amount), "currency_code": currency}
        },
    })

    plan_res = httpx.post(
        f"{BASE_URL}/v1/billing/plans",
        headers=_headers(),
        json={
            "product_id": product_id,
            "name": plan_name,
            "status": "ACTIVE",
            "billing_cycles": billing_cycles,
            "payment_preferences": payment_preferences,
        },
        timeout=TIMEOUT,
    )
    if plan_res.status_code >= 400:
        # raise_for_status() alone only surfaces the status code ("422 Unknown
        # Error") - PayPal puts the actual validation reason (which field/cycle
        # is invalid) in the response body, so log and raise with it included.
        print(f"[paypal_service] create_plan failed ({plan_res.status_code}): {plan_res.text}")
        raise RuntimeError(f"PayPal create_plan failed ({plan_res.status_code}): {plan_res.text}")
    return plan_res.json()["id"]


def create_subscription(client_id: int, plan_name: str, amount: float, currency: str = "ILS",
                         setup_fee: float = 0, return_url: str = None, cancel_url: str = None) -> dict:
    headers = _headers()
    return_url = return_url or f"{PUBLIC_APP_URL}/api/payment-success?client_id={client_id}"
    cancel_url = cancel_url or f"{PUBLIC_APP_URL}/chat/"

    # Step 1 — create a product
    product_res = httpx.post(
        f"{BASE_URL}/v1/catalogs/products",
        headers=headers,
        json={"name": plan_name, "type": "SERVICE", "category": "SOFTWARE"},
        timeout=TIMEOUT,
    )
    product_res.raise_for_status()
    product_id = product_res.json()["id"]

    # Step 2 — create a monthly billing plan on that product, with the setup
    # fee attached so it's charged in the same approval as the first payment
    plan_id = create_plan(product_id, plan_name, amount, currency, setup_fee)

    # Step 3 — create the subscription
    sub_res = httpx.post(
        f"{BASE_URL}/v1/billing/subscriptions",
        headers=headers,
        json={
            "plan_id": plan_id,
            "custom_id": str(client_id),
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "SUBSCRIBE_NOW",
            },
        },
        timeout=TIMEOUT,
    )
    sub_res.raise_for_status()
    sub = sub_res.json()

    approve_url = next(
        (link["href"] for link in sub.get("links", []) if link["rel"] == "approve"),
        None,
    )

    return {
        "subscription_id": sub.get("id"),
        "status": sub.get("status"),
        "plan_id": plan_id,
        "approve_url": approve_url,
        "setup_fee": setup_fee,
    }


# ─── Webhooks ─────────────────────────────────────────────────────────────────

def verify_webhook_signature(headers: dict, body: bytes, webhook_id: str) -> bool:
    """Verify a PayPal webhook event's signature via PayPal's own verification API.
    Returns False (never raises) on any failure, so a misconfigured/unreachable
    verification call fails closed rather than crashing the webhook handler."""
    headers = {k.lower(): v for k, v in headers.items()}
    try:
        payload = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": json.loads(body),
        }
        response = httpx.post(
            f"{BASE_URL}/v1/notifications/verify-webhook-signature",
            headers=_headers(),
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        print(f"[paypal_service] webhook signature verification error: {e}")
        return False


def cancel_subscription(subscription_id: str) -> dict:
    response = httpx.post(
        f"{BASE_URL}/v1/billing/subscriptions/{subscription_id}/cancel",
        headers=_headers(),
        json={"reason": "Cancelled by admin"},
        timeout=TIMEOUT,
    )
    return {
        "subscription_id": subscription_id,
        "cancelled": response.status_code == 204,
    }


def get_subscription_status(subscription_id: str) -> dict:
    response = httpx.get(
        f"{BASE_URL}/v1/billing/subscriptions/{subscription_id}",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    return {
        "subscription_id": subscription_id,
        "status": data.get("status"),
        "plan_id": data.get("plan_id"),
        "start_time": data.get("start_time"),
        "next_billing_time": data.get("billing_info", {}).get("next_billing_time"),
    }


def get_plan(plan_id: str) -> dict:
    response = httpx.get(
        f"{BASE_URL}/v1/billing/plans/{plan_id}",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return {"plan_id": plan_id, "product_id": data.get("product_id"), "name": data.get("name")}


def revise_subscription_plan(subscription_id: str, new_plan_id: str, return_url: str, cancel_url: str) -> dict:
    """Upgrade path: revise the live subscription onto a new plan. The
    subscriber must approve the price change via the returned approve_url;
    the new price takes effect from the NEXT billing cycle (no proration) -
    which is exactly the product behavior we promise in the upgrade panel."""
    response = httpx.post(
        f"{BASE_URL}/v1/billing/subscriptions/{subscription_id}/revise",
        headers=_headers(),
        json={
            "plan_id": new_plan_id,
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    approve_url = next(
        (link["href"] for link in data.get("links", []) if link["rel"] == "approve"),
        None,
    )
    return {"subscription_id": subscription_id, "new_plan_id": new_plan_id, "approve_url": approve_url}


def list_subscription_transactions(subscription_id: str, start_time: str, end_time: str) -> list:
    """Completed/attempted charges on a subscription - PayPal is the source of
    truth for what a client actually paid; we deliberately don't keep a
    parallel payments table."""
    response = httpx.get(
        f"{BASE_URL}/v1/billing/subscriptions/{subscription_id}/transactions",
        headers=_headers(),
        params={"start_time": start_time, "end_time": end_time},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    transactions = []
    for t in response.json().get("transactions", []):
        gross = (t.get("amount_with_breakdown") or {}).get("gross_amount") or {}
        transactions.append({
            "date": t.get("time"),
            "amount": gross.get("value"),
            "currency": gross.get("currency_code"),
            "status": t.get("status"),
        })
    return transactions


# ─── Invoices ─────────────────────────────────────────────────────────────────

def create_invoice(client_id: int, amount: float, description: str,
                    client_name: str = "", client_email: str = "", address: str = "",
                    business_name: str = "", business_tax_id: str = "") -> dict:
    """Every client is an Israeli SMB (see CLAUDE.md), so the recipient address is
    hardcoded to country_code IL rather than asking checkout for a country field."""
    headers = _headers()
    invoice_number = f"INV-{client_id}-{int(datetime.now().timestamp())}"

    billing_info = {}
    if business_name:
        billing_info["business_name"] = business_name
    if client_name:
        given, _, surname = client_name.strip().partition(" ")
        billing_info["name"] = {"given_name": given, "surname": surname or given}
    if client_email:
        billing_info["email_address"] = client_email
    if address:
        billing_info["address"] = {"address_line_1": address, "country_code": "IL"}
    if business_tax_id:
        # Invoicing v2's BillingInfo has no dedicated tax-id field for the recipient
        # (only Invoicer, the merchant side, has one) - additional_info is the
        # documented free-text slot for extra recipient reference details.
        billing_info["additional_info"] = f"עוסק מורשה / ח.פ: {business_tax_id}"

    invoice_payload = {
        "detail": {
            "invoice_number": invoice_number,
            "currency_code": "ILS",
            "note": description,
        },
        "items": [
            {
                "name": description,
                "quantity": "1",
                "unit_amount": {"currency_code": "ILS", "value": str(amount)},
            }
        ],
    }
    if billing_info:
        invoice_payload["primary_recipients"] = [{"billing_info": billing_info}]

    # Step 1 — create draft invoice
    create_res = httpx.post(
        f"{BASE_URL}/v2/invoicing/invoices",
        headers=headers,
        json=invoice_payload,
        timeout=TIMEOUT,
    )
    create_res.raise_for_status()
    invoice_id = create_res.json().get("id")

    # Step 2 — send the invoice
    httpx.post(
        f"{BASE_URL}/v2/invoicing/invoices/{invoice_id}/send",
        headers=headers,
        json={"send_to_recipient": True},
        timeout=TIMEOUT,
    )

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "amount": amount,
        "description": description,
        "status": "SENT",
    }
