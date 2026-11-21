"""BLE Footfall: anonymous visit counting from ESPHome BLE scanner nodes.

The nodes fire ``esphome.ble_footfall`` events listing the devices they hear.
This integration merges those by device, estimates positions, and counts each
device that comes close and leaves again as one visit.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DOOR,
    CONF_NODES,
    CONF_RSSI_OFFSET,
    CONF_X,
    CONF_Y,
    DOMAIN,
    EVENT_SIGHTINGS,
    EVENT_VISIT,
    SIGNAL_UPDATE,
    SWEEP_SECONDS,
)
from .tracker import Engine, Node, Settings

_LOGGER = logging.getLogger(__name__)

NODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_X): vol.Coerce(float),
        vol.Required(CONF_Y): vol.Coerce(float),
        vol.Optional(CONF_DOOR): cv.string,
        vol.Optional(CONF_RSSI_OFFSET, default=0.0): vol.Coerce(float),
    }
)

# Every tuning knob of tracker.Settings is accepted under the same name.
_SETTINGS_SCHEMA = {
    vol.Optional(f.name, default=f.default): vol.Any(None, vol.Coerce(float))
    if f.default is None
    else vol.Coerce(float)
    for f in fields(Settings)
}
_INT_SETTINGS = {"min_nodes", "min_hits", "max_tracks"}

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_NODES): vol.All(
                    {cv.string: NODE_SCHEMA}, vol.Length(min=1)
                ),
                **_SETTINGS_SCHEMA,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def parse_sightings(raw: str) -> dict[str, float]:
    """Parse the node payload ``"1a2b3c4d:-61;9f8e7d6c:-74;"``."""
    out: dict[str, float] = {}
    for item in raw.split(";"):
        dev, sep, rssi = item.partition(":")
        if not sep or not dev:
            continue
        try:
            out[dev] = float(rssi)
        except ValueError:
            continue
    return out


class FootfallHub:
    """Owns the engine and connects it to the event bus."""

    def __init__(self, hass: HomeAssistant, engine: Engine) -> None:
        self.hass = hass
        self.engine = engine
        self._unknown_nodes: set[str] = set()

    @callback
    def handle_sightings(self, event: Event) -> None:
        node = event.data.get("node")
        raw = event.data.get("devs")
        if not isinstance(node, str) or not isinstance(raw, str):
            return
        sightings = parse_sightings(raw)
        if not sightings:
            return
        if not self.engine.ingest(node, event.time_fired.timestamp(), sightings):
            if node not in self._unknown_nodes:
                self._unknown_nodes.add(node)
                _LOGGER.warning(
                    "Ignoring sightings from node '%s': it is not listed under "
                    "%s: %s: in configuration.yaml",
                    node,
                    DOMAIN,
                    CONF_NODES,
                )

    @callback
    def sweep(self, now=None) -> None:
        for visit in self.engine.sweep(dt_util.utcnow().timestamp()):
            self.hass.bus.async_fire(
                EVENT_VISIT,
                {
                    "entry": visit.entry,
                    "exit": visit.exit,
                    "dwell": round(visit.dwell, 1),
                    "peak_node": visit.peak_node,
                    "peak_rssi": round(visit.peak_rssi),
                    "path": [list(p) for p in visit.path],
                },
            )
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)


def _keep_out_of_recorder(hass: HomeAssistant) -> None:
    """Stop the recorder from writing every sighting event to the database.

    The events arrive every couple of seconds and contain (hashed) device ids,
    so they should not end up on disk. The recorder has no public hook for an
    integration to exclude its own event type, so this adds it to the live
    exclude set and falls back to telling the user how to do it by hand.
    """
    if "recorder" not in hass.config.components:
        return
    try:
        from homeassistant.components import recorder  # noqa: PLC0415

        recorder.get_instance(hass).exclude_event_types.add(EVENT_SIGHTINGS)
    except Exception:  # noqa: BLE001 - any failure here must not block setup
        _LOGGER.warning(
            "Could not exclude '%s' events from the recorder automatically. Add "
            "this to configuration.yaml to keep them out of the database:\n"
            "recorder:\n  exclude:\n    event_types:\n      - %s",
            EVENT_SIGHTINGS,
            EVENT_SIGHTINGS,
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    nodes = [
        Node(
            name=name,
            x=node[CONF_X],
            y=node[CONF_Y],
            door=node.get(CONF_DOOR),
            rssi_offset=node[CONF_RSSI_OFFSET],
        )
        for name, node in conf[CONF_NODES].items()
    ]
    settings = Settings(
        **{
            f.name: int(conf[f.name]) if f.name in _INT_SETTINGS else conf[f.name]
            for f in fields(Settings)
        }
    )
    hub = FootfallHub(hass, Engine(nodes, settings))
    hass.data[DOMAIN] = hub

    _keep_out_of_recorder(hass)
    hass.bus.async_listen(EVENT_SIGHTINGS, hub.handle_sightings)
    async_track_time_interval(hass, hub.sweep, timedelta(seconds=SWEEP_SECONDS))

    hass.async_create_task(
        async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config)
    )
    return True
