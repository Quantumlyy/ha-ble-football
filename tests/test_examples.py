"""Every example config must be accepted by the integration."""

from pathlib import Path

import pytest
import yaml

from homeassistant.setup import async_setup_component

from custom_components.ble_footfall.const import DOMAIN

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.yaml"))

# Derived from the layout: the signal expected at 75% of the smallest node spacing.
EXPECTED_CONFIRM_RSSI = {
    "configuration.yaml": -72.9,
    "site-doors-at-ends.yaml": -70.3,
    "site-door-on-dividing-line.yaml": -66.9,
}


def test_all_examples_are_covered():
    assert {p.name for p in EXAMPLES} == set(EXPECTED_CONFIRM_RSSI)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
async def test_example_loads(hass, path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()

    engine = hass.data[DOMAIN].engine
    assert set(engine.nodes) == {
        "footfall-door-a",
        "footfall-door-b",
        "footfall-side-c",
        "footfall-side-d",
    }
    assert engine.doors == ["A", "B"]
    assert engine.confirm_rssi == pytest.approx(
        EXPECTED_CONFIRM_RSSI[path.name], abs=0.1
    )
    assert hass.states.get("sensor.footfall_visits") is not None
