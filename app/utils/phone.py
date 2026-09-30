"""Phone number normalisation shared by imports and WhatsApp links.

Numbers are stored in international format ("+2348031234567") so that
wa.me links work and the same customer is recognised however the number
was typed ("0803 123 4567", "234-803-123-4567", "+234 803 123 4567").
"""
import re
from typing import Optional

# Countries where a leading 0 is a domestic trunk prefix that must be dropped
# when writing the number internationally (0803... -> +234803...).
# Countries that keep the 0 (e.g. Ivory Coast +225, Italy +39) are deliberately absent.
TRUNK_ZERO_CODES = {
    "234", "233", "254", "255", "256", "250", "27", "44", "91", "49", "33",
    "971", "20", "212", "62", "61", "63", "92", "880", "31", "32", "41", "43",
}

MIN_DIGITS = 8   # shortest plausible full international number
MAX_DIGITS = 15  # E.164 maximum


def _clean_dial_code(dial_code: Optional[str]) -> str:
    digits = re.sub(r"\D", "", dial_code or "")
    return digits or "234"


def normalize_phone(raw: Optional[str], default_dial_code: str = "+234") -> Optional[str]:
    """Return the number as "+<digits>", or None if it can't be a valid phone number."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Excel often hands phone numbers over as floats ("8031234567.0").
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]

    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None

    dial = _clean_dial_code(default_dial_code)

    if has_plus:
        international = digits
    elif digits.startswith("00"):
        international = digits[2:]
    elif digits.startswith("0"):
        international = dial + digits.lstrip("0") if dial in TRUNK_ZERO_CODES else dial + digits
    elif digits.startswith(dial) and len(digits) >= len(dial) + 7:
        # Already includes the country code, just missing the "+".
        international = digits
    else:
        international = dial + digits

    # "+234 0803..." -> "+234803..."
    for code in TRUNK_ZERO_CODES:
        if international.startswith(code + "0"):
            international = code + international[len(code) + 1:]
            break

    if not (MIN_DIGITS <= len(international) <= MAX_DIGITS):
        return None
    return "+" + international
