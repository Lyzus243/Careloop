from app.utils.phone import normalize_phone, display_phone


def test_accepts_international_with_plus_and_spaces():
    assert normalize_phone("+234 803 123 4567") == "2348031234567"
    assert normalize_phone("+234-803-123-4567") == "2348031234567"
    assert normalize_phone("+234(803)1234567") == "2348031234567"


def test_accepts_double_zero_prefix():
    assert normalize_phone("00234 803 123 4567") == "2348031234567"


def test_accepts_bare_digits_with_country_code():
    assert normalize_phone("2348031234567") == "2348031234567"


def test_rejects_local_format_without_country_code():
    # Careloop requires full international numbers; 0803... is ambiguous.
    assert normalize_phone("08031234567") is None


def test_rejects_junk_and_empty():
    for value in ("", None, "   ", "not a phone", "12", "+0000"):
        assert normalize_phone(value) is None


def test_rejects_invalid_number_for_country():
    assert normalize_phone("+234 000 000 0000") is None


def test_uk_number():
    assert normalize_phone("+44 7911 123456") == "447911123456"


def test_display_phone_round_trip():
    assert display_phone("2348031234567").startswith("+234")
