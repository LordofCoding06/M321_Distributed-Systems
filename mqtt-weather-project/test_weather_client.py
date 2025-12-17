from datetime import datetime, timezone

from weather_client import parse_iso, validate


def test_validate_ok_for_reasonable_inputs():
    ok, problems = validate("20.5", "50")
    assert ok
    assert problems == []


def test_validate_flags_out_of_range_numbers():
    ok, problems = validate("-999", "150")

    assert ok is False
    assert any("invalid temperature" in msg for msg in problems)
    assert any("invalid humidity" in msg for msg in problems)


def test_validate_flags_non_numeric_strings():
    ok, problems = validate("abc", "xyz")

    assert not ok
    assert any("temperature not a number" in msg for msg in problems)
    assert any("humidity not a number" in msg for msg in problems)


def test_parse_iso_handles_valid_and_invalid_inputs():
    parsed = parse_iso("2024-01-02T12:34:56Z")
    assert isinstance(parsed, datetime)
    assert parsed.tzinfo == timezone.utc
    assert (parsed.year, parsed.month, parsed.day) == (2024, 1, 2)
    assert (parsed.hour, parsed.minute, parsed.second) == (12, 34, 56)

    assert parse_iso("not-a-timestamp") is None


def test_parse_iso_returns_none_for_non_strings():
    assert parse_iso(None) is None
    assert parse_iso(12345) is None
