from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode
from app.services.phone import normalise_phone


def fee_amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
            raise ValueError("Delivery fee must be between 0 and 9,999,999,999.99")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Delivery fee must have at most two decimal places")
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid delivery fee") from None


def whatsapp_number(value):
    if not isinstance(value, str):
        raise ValueError("Enter a business WhatsApp number with country code")
    raw = value.strip()
    if not raw:
        return None
    if not re.fullmatch(r"[+0-9 ()-]+", raw):
        raise ValueError("Enter a valid WhatsApp number")
    digits = re.sub(r"\D", "", raw) if raw.startswith("+") else normalise_phone(raw).lstrip("+")
    if not re.fullmatch(r"[1-9][0-9]{7,14}", digits):
        raise ValueError("Enter a WhatsApp number with country code, e.g. +2348031234567")
    return digits


def delivery_settings(user, data):
    # Delivery is always arranged on WhatsApp; ignore legacy fixed-fee inputs.
    number = whatsapp_number(data.get("delivery_whatsapp", user.delivery_whatsapp or ""))
    if not number:
        raise ValueError("Enter the business WhatsApp number for delivery and pickup arrangements")
    return "whatsapp", Decimal("0.00"), number


def delivery_quote(user, delivery_type):
    if delivery_type == "pickup":
        return "pickup", Decimal("0.00")
    return "whatsapp", None


def quote_matches(mode, fee, expected_mode, expected_fee):
    if expected_mode != mode:
        return False
    if fee is None:
        return expected_fee is None
    try:
        return fee_amount(expected_fee) == fee
    except ValueError:
        return False


def delivery_contact_url(number, order_id, code, name, address):
    if not number:
        return None
    text = (f"Hello, I am {name}. Please confirm the delivery fee and combined payment total "
            f"for order #{order_id}." + (f" Tracking code: {code}." if code else "") +
            f" Delivery address: {address}. I will pay once for the items and delivery together after we agree the fee.")
    return "https://wa.me/" + number + "?" + urlencode({"text": text})

def pickup_contact_url(number, order_id=None, code=None, name=None, address=None):
    if not number:
        return None
    if order_id is None:
        text = "Hello, please confirm your pickup location and collection times before I place an order."
    else:
        text = (f"Hello, I am {name}. I have placed pickup order #{order_id} and uploaded my payment receipt for review."
                + (f" Tracking code: {code}." if code else "")
                + (f" Pickup address shown: {address}." if address else "")
                + " Please confirm the pickup location and when I can collect my order.")
    return "https://wa.me/" + number + "?" + urlencode({"text": text})

