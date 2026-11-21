"""Sensors for BLE Footfall."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import slugify

from . import FootfallHub
from .const import DOMAIN, SIGNAL_UPDATE
from .tracker import REJECT_REASONS, Engine

_REJECT_NAMES = {
    "noise": "Ignored: heard too briefly",
    "far": "Ignored: never close",
    "outside": "Ignored: one node only",
    "stationary": "Ignored: stayed too long",
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    hub: FootfallHub = hass.data[DOMAIN]
    engine = hub.engine

    entities: list[SensorEntity] = [
        PresentSensor(engine),
        CounterSensor("visits", "Footfall visits", lambda: engine.total),
    ]
    for door in engine.doors:
        key = slugify(door)
        entities.append(
            CounterSensor(
                f"entered_{key}",
                f"Footfall entered via {door}",
                lambda d=door: engine.entries[d],
            )
        )
        entities.append(
            CounterSensor(
                f"left_{key}",
                f"Footfall left via {door}",
                lambda d=door: engine.exits[d],
            )
        )
    for entry, exit_ in engine.transits:
        name = (
            f"Footfall in and out via {entry}"
            if entry == exit_
            else f"Footfall {entry} to {exit_}"
        )
        entities.append(
            CounterSensor(
                f"transit_{slugify(entry)}_{slugify(exit_)}",
                name,
                lambda pair=(entry, exit_): engine.transits[pair],
            )
        )
    for reason in REJECT_REASONS:
        entities.append(
            CounterSensor(
                f"ignored_{reason}",
                f"Footfall {_REJECT_NAMES[reason].lower()}",
                lambda r=reason: engine.rejected[r],
                diagnostic=True,
            )
        )
    async_add_entities(entities)


class CounterSensor(RestoreSensor):
    """An ever-increasing count that survives Home Assistant restarts."""

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:walk"

    def __init__(
        self, key: str, name: str, getter: Callable[[], int], diagnostic: bool = False
    ) -> None:
        self._getter = getter
        self._base = 0
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:filter-outline"

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._base = int(last.native_value)
            except (TypeError, ValueError):
                self._base = 0
        self._attr_native_value = self._base + self._getter()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        value = self._base + self._getter()
        if value != self._attr_native_value:
            self._attr_native_value = value
            self.async_write_ha_state()


class PresentSensor(SensorEntity):
    """Devices in range right now that would count as a visit if they left."""

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"
    _attr_name = "Footfall present"
    _attr_unique_id = f"{DOMAIN}_present"
    # Positions change every couple of seconds; keep them out of the database.
    _unrecorded_attributes = frozenset({"positions"})

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._positions: list[tuple[float, float]] = []
        self._attr_native_value = 0

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh)
        )

    @property
    def extra_state_attributes(self) -> dict[str, list[list[float]]]:
        return {"positions": [list(p) for p in self._positions]}

    @callback
    def _refresh(self) -> None:
        positions = self._engine.present()
        if positions != self._positions:
            self._positions = positions
            self._attr_native_value = len(positions)
            self.async_write_ha_state()
