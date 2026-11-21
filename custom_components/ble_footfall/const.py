"""Constants for the BLE Footfall integration."""

DOMAIN = "ble_footfall"

# Fired by the ESPHome nodes: {"node": "footfall-door-a", "devs": "1a2b3c4d:-61;..."}
EVENT_SIGHTINGS = "esphome.ble_footfall"
# Fired by this integration for every counted visit. Carries no device id.
EVENT_VISIT = "ble_footfall_visit"

SIGNAL_UPDATE = "ble_footfall_update"

CONF_NODES = "nodes"
CONF_X = "x"
CONF_Y = "y"
CONF_DOOR = "door"
CONF_RSSI_OFFSET = "rssi_offset"

SWEEP_SECONDS = 2
