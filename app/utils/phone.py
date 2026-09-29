"""Phone number normalization for the WhatsApp Cloud API.

Meta expects recipient numbers in E.164 form without the leading '+', e.g.
2348031234567. Careloop stores customer phone numbers as free text, so numbers
are normalized at send time only -- customers.phone_number is never rewritten.

Careloop requires full international numbers. A local-format number such as
"08031234567" has no unambiguous country, so it is rejected rather than guessed
at; campaign previews surface the count as "invalid number" so the vendor can
correct their records.
"""
import re
from typing import Optional

try:
    import phonenumbers
except ImportError:  # pragma: no cover
    phonenumbers = None

_SEPARATORS = re.compile(r"[\s\-(). ‐-―]")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Return E.164 digits without '+', or None when the number is unusable.

    Accepts '+234 803 123 4567', '00234...', '234-803-123-4567'.
    Rejects '08031234567' (no country code), empty values and invalid numbers.
    """
    if not raw:
        return None

    cleaned = _SEPARATORS.sub("", str(raw)).strip()
    if not cleaned:
        return None

    # International prefix 00 (and its 011 North American variant) means '+'.
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if not cleaned.startswith("+"):
        # Bare digits are only accepted when they already carry a country code.
        if not cleaned.isdigit():
            return None
        cleaned = "+" + cleaned

    if len(cleaned) < 8 or len(cleaned) > 17:
        return None

    if phonenumbers is None:  # pragma: no cover - dependency always installed
        digits = cleaned[1:]
        return digits if digits.isdigit() and 10 <= len(digits) <= 15 else None

    try:
        parsed = phonenumbers.parse(cleaned, None)
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return e164.lstrip("+")


def display_phone(e164_digits: Optional[str]) -> Optional[str]:
    """Format stored digits back to a readable international form for the UI."""
    if not e164_digits:
        return None
    if phonenumbers is None:  # pragma: no cover
        return "+" + e164_digits
    try:
        parsed = phonenumbers.parse("+" + e164_digits.lstrip("+"), None)
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except Exception:
        return "+" + e164_digits
