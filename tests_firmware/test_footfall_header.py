"""Unit tests for esphome/common/footfall.h, run natively on the desktop."""

from __future__ import annotations

import re

import pytest

from .reference import anon_id, parse_sightings

SALT = "test-salt"
DAY = 20778
FLOOR = -90

PHONE = 0xA1B2C3D4E5F6
WATCH = 0x0102030405


def answer(lines: list[str], prefix: str) -> str:
    """The text after `prefix` on the single output line that starts with it."""
    (line,) = [ln for ln in lines if ln.startswith(prefix + " ") or ln == prefix]
    return line[len(prefix) :].strip()


def drained(footfall, commands: list[str]) -> dict[str, float]:
    return parse_sightings(answer(footfall([*commands, f"drain {SALT} {DAY}"]), "drain"))


def test_keeps_the_strongest_reading_per_device(footfall):
    report = drained(
        footfall,
        [
            f"observe {PHONE:x} -80 {FLOOR}",
            f"observe {PHONE:x} -60 {FLOOR}",
            f"observe {PHONE:x} -70 {FLOOR}",
            f"observe {WATCH:x} -75 {FLOOR}",
        ],
    )
    assert report == {anon_id(PHONE, SALT, DAY): -60.0, anon_id(WATCH, SALT, DAY): -75.0}


def test_readings_below_the_floor_are_ignored(footfall):
    out = footfall(
        [
            f"observe {PHONE:x} -91 {FLOOR}",
            "pending",
            f"observe {PHONE:x} -90 {FLOOR}",
            "pending",
        ]
    )
    assert [ln for ln in out if ln.startswith("pending")] == ["pending 0", "pending 1"]


def test_rssi_is_clamped_to_what_fits_in_the_report(footfall):
    report = drained(
        footfall,
        [f"observe {PHONE:x} 12 -300", f"observe {WATCH:x} -250 -300"],
    )
    assert report == {anon_id(PHONE, SALT, DAY): 0.0, anon_id(WATCH, SALT, DAY): -127.0}


def test_drain_empties_the_buffer(footfall):
    out = footfall(
        [
            f"observe {PHONE:x} -60 {FLOOR}",
            "pending",
            f"drain {SALT} {DAY}",
            "pending",
            f"drain {SALT} {DAY}",
        ]
    )
    assert [ln for ln in out if ln.startswith("pending")] == ["pending 1", "pending 0"]
    assert out[-1].strip() == "drain"


def test_clear_empties_the_buffer(footfall):
    out = footfall([f"observe {PHONE:x} -60 {FLOOR}", "clear", "pending"])
    assert answer(out, "pending") == "0"


def test_report_format(footfall):
    payload = answer(
        footfall(
            [
                f"observe {PHONE:x} -58 {FLOOR}",
                f"observe {WATCH:x} -74 {FLOOR}",
                f"drain {SALT} {DAY}",
            ]
        ),
        "drain",
    )
    assert re.fullmatch(r"(?:[0-9a-f]{8}:-?\d{1,3};)+", payload), payload
    assert parse_sightings(payload) == {
        anon_id(PHONE, SALT, DAY): -58.0,
        anon_id(WATCH, SALT, DAY): -74.0,
    }


@pytest.mark.parametrize(
    ("addr", "salt", "day"),
    [
        (PHONE, SALT, DAY),
        (WATCH, SALT, DAY),
        (0x000000000000, SALT, 0),
        (0xFFFFFFFFFFFF, "another-salt", 4294967295),
        (PHONE, "x", 1),
    ],
)
def test_id_matches_the_reference_hash(footfall, addr, salt, day):
    assert answer(footfall([f"id {addr:x} {salt} {day}"]), "id") == anon_id(addr, salt, day)


def test_id_is_always_eight_hex_digits(footfall):
    # Find an address whose hash starts with a zero and check it is not shortened.
    addr = next(a for a in range(1, 100000) if anon_id(a, SALT, DAY).startswith("0"))
    out = answer(footfall([f"id {addr:x} {SALT} {DAY}"]), "id")
    assert out == anon_id(addr, SALT, DAY)
    assert len(out) == 8


def test_id_changes_with_the_day_and_with_the_salt(footfall):
    out = footfall(
        [
            f"id {PHONE:x} {SALT} {DAY}",
            f"id {PHONE:x} {SALT} {DAY + 1}",
            f"id {PHONE:x} other-salt {DAY}",
            f"id {PHONE:x} {SALT} {DAY}",
        ]
    )
    today, tomorrow, other_salt, today_again = (ln.split()[1] for ln in out)
    assert today == today_again, "every node must produce the same id"
    assert len({today, tomorrow, other_salt}) == 3


def test_strongest_signal(footfall):
    out = footfall(
        [
            "strongest",
            f"observe {PHONE:x} -71 {FLOOR}",
            f"observe {WATCH:x} -64 {FLOOR}",
            "strongest",
        ]
    )
    assert out == ["strongest nan", "strongest -64"]


def test_buffer_is_capped_and_keeps_the_strongest_devices(footfall):
    limit = int(answer(footfall(["max"]), "max"))
    count = limit + 12
    # Device i is heard at -120 + i dBm, so later devices are stronger.
    commands = [f"observe {0x100 + i:x} {-120 + i} -127" for i in range(count)]
    out = footfall([*commands, "pending", f"drain {SALT} {DAY}"])
    assert answer(out, "pending") == str(limit)
    report = parse_sightings(answer(out, "drain"))
    expected = {
        anon_id(0x100 + i, SALT, DAY): float(-120 + i) for i in range(count - limit, count)
    }
    assert report == expected


def test_full_buffer_ignores_a_weaker_newcomer(footfall):
    limit = int(answer(footfall(["max"]), "max"))
    commands = [f"observe {0x100 + i:x} -60 {FLOOR}" for i in range(limit)]
    newcomer = 0xABCDEF
    out = footfall([*commands, f"observe {newcomer:x} -80 {FLOOR}", f"drain {SALT} {DAY}"])
    report = parse_sightings(answer(out, "drain"))
    assert len(report) == limit
    assert anon_id(newcomer, SALT, DAY) not in report


def test_known_device_is_updated_even_when_the_buffer_is_full(footfall):
    limit = int(answer(footfall(["max"]), "max"))
    commands = [f"observe {0x100 + i:x} -60 {FLOOR}" for i in range(limit)]
    out = footfall([*commands, f"observe {0x100:x} -40 {FLOOR}", f"drain {SALT} {DAY}"])
    report = parse_sightings(answer(out, "drain"))
    assert len(report) == limit
    assert report[anon_id(0x100, SALT, DAY)] == -40.0
