from datetime import datetime, timezone

from weather_client import validate, parse_iso


def test_validate_accepts_valid_values():
    # --- Arrange ---
    temp = "20.5"
    hum = "50"

    # --- Act ---
    is_valid, errors = validate(temp, hum)

    # --- Assert ---
    assert is_valid is True
    assert errors == []


def test_validate_rejects_invalid_values():
    # --- Arrange ---
    temp = "-999"
    hum = "150"

    # --- Act ---
    is_valid, errors = validate(temp, hum)

    # --- Assert ---
    assert is_valid is False
    assert any("invalid temperature" in e for e in errors)
    assert any("invalid humidity" in e for e in errors)


def test_parse_iso_valid_and_invalid():
    # --- Arrange ---
    valid_ts = "2024-01-02T12:34:56Z"
    invalid_ts = "not-a-timestamp"

    # --- Act ---
    dt_valid = parse_iso(valid_ts)
    dt_invalid = parse_iso(invalid_ts)

    # --- Assert ---
    assert isinstance(dt_valid, datetime)
    assert dt_valid.tzinfo == timezone.utc
    assert (
        dt_valid.year == 2024
        and dt_valid.month == 1
        and dt_valid.day == 2
        and dt_valid.hour == 12
        and dt_valid.minute == 34
        and dt_valid.second == 56
    )
    assert dt_invalid is None
