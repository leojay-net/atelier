import pytest

from atelier.spec.types import parse_fraction, parse_timecode


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        (5, 5.0),
        (2.5, 2.5),
        ("5", 5.0),
        ("5s", 5.0),
        ("00:05", 5.0),
        ("0:05", 5.0),
        ("00:48.2", 48.2),
        ("1:30:00", 5400.0),
        ("90", 90.0),
    ],
)
def test_timecode(raw: object, seconds: float) -> None:
    assert parse_timecode(raw) == seconds


@pytest.mark.parametrize(
    "raw", ["", "nan", "inf", "1:60", "1:61:00", "a:05", "-1", -1, True, "1:2:3:4", "1.5:00"]
)
def test_timecode_rejects(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_timecode(raw)


@pytest.mark.parametrize(("raw", "value"), [("12%", 0.12), (" 50 % ", 0.5), (0.3, 0.3)])
def test_fraction(raw: object, value: float) -> None:
    assert parse_fraction(raw) == pytest.approx(value)


def test_fraction_rejects() -> None:
    with pytest.raises(ValueError):
        parse_fraction("abc%")
