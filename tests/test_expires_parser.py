from app.core.security import parse_expires


def test_units():
    assert parse_expires("30s") == 30
    assert parse_expires("15m") == 900
    assert parse_expires("2h") == 7200
    assert parse_expires("7d") == 604800


def test_invalid_falls_back():
    assert parse_expires("") == 7200
    assert parse_expires("abc") == 7200
    assert parse_expires("10y") == 7200


def test_rounding():
    assert parse_expires("1h") == 3600
    assert parse_expires("0s") == 0
