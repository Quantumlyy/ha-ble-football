import random

import pytest

from .sim import (
    IN_AND_OUT_A,
    PAST_DOOR_A_OUTSIDE,
    PAST_SIDE_C_OUTSIDE,
    THROUGH_A_TO_B,
    THROUGH_B_TO_A,
    make_engine,
    run,
    standing_at,
)


def test_walk_through_is_one_visit_with_direction():
    engine = make_engine()
    visits = run(engine, {"p1": (0.0, THROUGH_A_TO_B)}, until=60)
    assert len(visits) == 1, "four nodes heard it, but it is one visit"
    assert (visits[0].entry, visits[0].exit) == ("A", "B")
    assert engine.total == 1
    assert engine.entries == {"A": 1, "B": 0}
    assert engine.exits == {"A": 0, "B": 1}
    assert engine.transits[("A", "B")] == 1


def test_reverse_direction():
    engine = make_engine()
    visits = run(engine, {"p1": (0.0, THROUGH_B_TO_A)}, until=60)
    assert [(v.entry, v.exit) for v in visits] == [("B", "A")]


def test_in_and_out_through_the_same_door():
    engine = make_engine()
    visits = run(engine, {"p1": (0.0, IN_AND_OUT_A)}, until=60)
    assert [(v.entry, v.exit) for v in visits] == [("A", "A")]
    assert engine.transits[("A", "A")] == 1


def test_path_moves_from_entry_door_towards_exit_door():
    engine = make_engine()
    (visit,) = run(engine, {"p1": (0.0, THROUGH_A_TO_B)}, until=60)
    xs = [x for x, _ in visit.path]
    assert xs[0] < 3.0 and xs[-1] > 5.0
    assert all(0.0 <= x <= 8.0 for x in xs), "centroid stays between the nodes"


def test_passing_outside_a_door_is_not_counted():
    engine = make_engine()
    visits = run(engine, {"p1": (0.0, PAST_DOOR_A_OUTSIDE)}, until=60)
    assert visits == []
    assert engine.total == 0
    assert engine.rejected["outside"] == 1


def test_passing_behind_a_side_wall_is_not_counted():
    engine = make_engine()
    visits = run(engine, {"p1": (0.0, PAST_SIDE_C_OUTSIDE)}, until=60)
    assert visits == []
    assert engine.rejected["outside"] == 1


@pytest.mark.parametrize("path", [PAST_DOOR_A_OUTSIDE, PAST_SIDE_C_OUTSIDE])
@pytest.mark.parametrize("noise", [4.0, 6.0])
def test_outside_rejection_with_noise_and_a_wall(noise, path):
    """100 people walking past outside, 6 dB of wall or glass in the way."""
    rng = random.Random(99)
    engine = make_engine()
    walkers = {f"p{i}": (i * 40.0, path) for i in range(100)}
    visits = run(
        engine, walkers, until=100 * 40.0 + 60, rng=rng, noise=noise, wall_loss=6.0
    )
    print(f"noise={noise} dB: {len(visits)}/100 outside passers wrongly counted")
    assert len(visits) <= 10


def test_confirm_rssi_is_derived_from_the_layout():
    # Closest pair of nodes is 4.47 m apart; reach is 0.75 of that.
    assert make_engine().confirm_rssi == pytest.approx(-72.9, abs=0.1)
    assert make_engine(confirm_rssi=-80.0).confirm_rssi == -80.0


def test_stationary_device_is_never_counted():
    engine = make_engine()
    visits = run(engine, {"tv": (0.0, standing_at(4.0, 3.0, 1200))}, until=1300)
    assert visits == []
    assert engine.rejected["stationary"] == 1


def test_stationary_device_is_not_reported_as_present():
    engine = make_engine()
    run(engine, {"tv": (0.0, standing_at(4.0, 3.0, 1200))}, until=700)
    assert engine.tracked == 1
    assert engine.present() == []


def test_distant_device_is_ignored():
    engine = make_engine()
    visits = run(engine, {"far": (0.0, standing_at(4.0, 14.0, 30))}, until=120)
    assert visits == []
    assert engine.rejected["far"] == 1


def test_single_report_is_noise():
    engine = make_engine()
    engine.ingest("door-a", 0.0, {"blip": -50})
    assert engine.sweep(100.0) == []
    assert engine.rejected["noise"] == 1


def test_unknown_node_is_refused():
    engine = make_engine()
    assert engine.ingest("not-a-node", 0.0, {"x": -50}) is False
    assert engine.tracked == 0


def test_two_people_at_once():
    engine = make_engine()
    visits = run(
        engine,
        {"p1": (0.0, THROUGH_A_TO_B), "p2": (3.0, THROUGH_B_TO_A)},
        until=80,
    )
    assert sorted((v.entry, v.exit) for v in visits) == [("A", "B"), ("B", "A")]


def test_same_person_returning_later_counts_again():
    engine = make_engine()
    visits = run(
        engine,
        {"p1": (0.0, THROUGH_A_TO_B), "p1-new-mac": (120.0, THROUGH_B_TO_A)},
        until=220,
    )
    assert len(visits) == 2


def test_present_shows_positions_while_inside():
    engine = make_engine()
    run(engine, {"p1": (0.0, standing_at(4.0, 2.0, 30))}, until=10)
    (pos,) = engine.present()
    assert pos == pytest.approx((4.0, 2.0), abs=0.5)


def test_track_table_is_bounded():
    engine = make_engine(max_tracks=10)
    for i in range(50):
        engine.ingest("door-a", float(i), {f"d{i}": -60})
    assert engine.tracked == 10


@pytest.mark.parametrize("noise", [0.0, 4.0, 6.0])
def test_counts_hold_up_under_rssi_noise(noise):
    """100 people, alternating direction, 30 s apart, with noisy RSSI."""
    rng = random.Random(1234)
    engine = make_engine()
    walkers = {
        f"p{i}": (i * 30.0, THROUGH_A_TO_B if i % 2 == 0 else THROUGH_B_TO_A)
        for i in range(100)
    }
    visits = run(engine, walkers, until=100 * 30.0 + 60, rng=rng, noise=noise)
    right_way = engine.transits[("A", "B")] + engine.transits[("B", "A")]
    print(f"noise={noise} dB: counted {len(visits)}/100, direction ok {right_way}")
    assert 95 <= len(visits) <= 100
    assert right_way >= 90
