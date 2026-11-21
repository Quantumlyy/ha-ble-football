"""End-to-end test inside Home Assistant: node events in, sensors out."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.ble_footfall import parse_sightings
from custom_components.ble_footfall.const import (
    DOMAIN,
    EVENT_SIGHTINGS,
    EVENT_VISIT,
)

from .sim import NODES, THROUGH_A_TO_B, position_at, rssi_at

CONFIG = {
    DOMAIN: {
        "nodes": {
            "footfall-door-a": {"x": 0, "y": 2, "door": "A"},
            "footfall-door-b": {"x": 8, "y": 2, "door": "B"},
            "footfall-side-c": {"x": 4, "y": 4},
            "footfall-side-d": {"x": 4, "y": 0},
        },
        "gone_after": 20,
    }
}


def test_parse_sightings():
    assert parse_sightings("1a2b3c4d:-61;9f8e7d6c:-74;") == {
        "1a2b3c4d": -61.0,
        "9f8e7d6c": -74.0,
    }
    assert parse_sightings("") == {}
    assert parse_sightings("garbage;:-5;abc:xyz;ok:-40") == {"ok": -40.0}


async def _walk_through(hass, freezer, dev="1a2b3c4d"):
    """Replay one person walking from door A to door B as node events."""
    settings = hass.data[DOMAIN].engine.settings
    t = 0.0
    while (pos := position_at(THROUGH_A_TO_B, t)) is not None:
        for node in NODES:
            rssi = rssi_at(node, pos, settings)
            if rssi >= -90:
                hass.bus.async_fire(
                    EVENT_SIGHTINGS,
                    {"node": f"footfall-{node.name}", "devs": f"{dev}:{round(rssi)};"},
                )
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        t += 2.0


async def _let_visits_close(hass, freezer):
    for _ in range(15):
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()


async def test_walk_through_updates_sensors_and_fires_visit(hass, freezer):
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    visits = async_capture_events(hass, EVENT_VISIT)

    assert hass.states.get("sensor.footfall_visits").state == "0"

    await _walk_through(hass, freezer)
    await _let_visits_close(hass, freezer)

    assert hass.states.get("sensor.footfall_visits").state == "1"
    assert hass.states.get("sensor.footfall_entered_via_a").state == "1"
    assert hass.states.get("sensor.footfall_left_via_b").state == "1"
    assert hass.states.get("sensor.footfall_a_to_b").state == "1"
    assert hass.states.get("sensor.footfall_b_to_a").state == "0"
    assert hass.states.get("sensor.footfall_present").state == "0"

    assert len(visits) == 1
    data = visits[0].data
    assert (data["entry"], data["exit"]) == ("A", "B")
    assert "dev" not in data and "1a2b3c4d" not in str(data)


async def test_present_sensor_shows_position_while_inside(hass, freezer):
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    for _ in range(3):
        for name, rssi in (
            ("footfall-door-a", -76),
            ("footfall-door-b", -76),
            ("footfall-side-c", -68),
            ("footfall-side-d", -68),
        ):
            hass.bus.async_fire(EVENT_SIGHTINGS, {"node": name, "devs": f"abc:{rssi};"})
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    state = hass.states.get("sensor.footfall_present")
    assert state.state == "1"
    assert state.attributes["positions"] == [[4.0, 2.0]]


async def test_unknown_node_and_bad_payloads_are_ignored(hass, freezer, caplog):
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_SIGHTINGS, {"node": "someone-else", "devs": "abc:-50;"})
    hass.bus.async_fire(EVENT_SIGHTINGS, {"node": "footfall-door-a"})
    hass.bus.async_fire(EVENT_SIGHTINGS, {"devs": "abc:-50;"})
    await hass.async_block_till_done()
    assert hass.data[DOMAIN].engine.tracked == 0
    assert "someone-else" in caplog.text


async def test_setup_without_config_is_a_no_op(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    assert DOMAIN not in hass.data


async def test_invalid_config_is_rejected(hass):
    assert not await async_setup_component(hass, DOMAIN, {DOMAIN: {"nodes": {}}})


async def test_sighting_events_are_kept_out_of_the_recorder(recorder_mock, hass):
    from homeassistant.components import recorder

    assert EVENT_SIGHTINGS not in recorder.get_instance(hass).exclude_event_types
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    assert EVENT_SIGHTINGS in recorder.get_instance(hass).exclude_event_types


async def test_counters_continue_after_a_restart(hass, freezer):
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache_with_extra_data,
    )

    from homeassistant.core import State

    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("sensor.footfall_visits", "41"),
                {"native_value": 41, "native_unit_of_measurement": None},
            )
        ],
    )
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.footfall_visits").state == "41"

    await _walk_through(hass, freezer)
    await _let_visits_close(hass, freezer)
    assert hass.states.get("sensor.footfall_visits").state == "42"
