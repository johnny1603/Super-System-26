import base64
from datetime import datetime

import httpx

from agents.keys_agent import get_key

BASE_URL = "https://api-m.sandbox.paypal.com"
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

def create_subscription(client_id: int, plan_name: str, amount: float, currency: str = "ILS") -> dict:
    headers = _headers()

    # Step 1 — create a product
    product_res = httpx.post(
        f"{BASE_URL}/v1/catalogs/products",
        headers=headers,
        json={"name": plan_name, "type": "SERVICE", "category": "SOFTWARE"},
        timeout=TIMEOUT,
    )
    product_res.raise_for_status()
    product_id = product_res.json()["id"]

    # Step 2 — create a monthly billing plan on that product
    plan_res = httpx.post(
        f"{BASE_URL}/v1/billing/plans",
        headers=headers,
        json={
            "product_id": product_id,
            "name": plan_name,
            "status": "ACTIVE",
            "billing_cycles": [
                {
                    "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                    "pricing_scheme": {
                        "fixed_price": {"value": str(amount), "currency_code": currency}
                    },
                }
            ],
            "payment_preferences": {
                "auto_bill_outstanding": True,
                "payment_failure_threshold": 3,
            },
        },
        timeout=TIMEOUT,
    )
    plan_res.raise_for_status()
    plan_id = plan_res.json()["id"]

    # Step 3 — create the subscription
    sub_res = httpx.post(
        f"{BASE_URL}/v1/billing/subscriptions",
        headers=headers,
        json={"plan_id": plan_id, "custom_id": str(client_id)},
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
    }


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


# ─── Invoices ─────────────────────────────────────────────────────────────────

def create_invoice(client_id: int, amount: float, description: str) -> dict:
    headers = _headers()
    invoice_number = f"INV-{client_id}-{int(datetime.now().timestamp())}"

    # Step 1 — create draft invoice
    create_res = httpx.post(
        f"{BASE_URL}/v2/invoicing/invoices",
        headers=headers,
        json={
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
        },
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
