import re


def normalise_phone(raw: str) -> str:
    """Return a canonical form so 0803..., +234803..., 234803... all match.
    Falls back to the digits as-is when the shape isn't recognised."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if digits.startswith("0") and len(digits) == 11:
        return "+234" + digits[1:]
    if digits.startswith("234") and len(digits) == 13:
        return "+" + digits
    if len(digits) == 10:
        return "+234" + digits
    if raw.strip().startswith("+"):
        return "+" + digits
    return digits
