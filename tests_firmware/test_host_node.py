"""End-to-end test of the node's reporting, without hardware.

Builds esphome/common/footfall-report.yaml (the same file the real nodes use)
as a desktop program with ESPHome's host platform, feeds it made-up Bluetooth
observations, connects to it the way Home Assistant does, and checks the
event it sends.

The Bluetooth scan itself is not covered: that needs a real board.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import signal
import socket
import subprocess
import time

import pytest

from .reference import COMMON, anon_id, parse_sightings

aioesphomeapi = pytest.importorskip("aioesphomeapi")

NODE_NAME = "footfall-host-test"
SALT = "host-test-salt"
PHONE = 0xA1B2C3D4E5F6
WATCH = 0x0102030405
TOO_WEAK = 0xFFFFFFFFFFFF

CONFIG = """
substitutions:
  name: {name}
  report_interval: 500ms

esphome:
  name: ${{name}}

host:
  mac_address: "06:35:69:ab:f6:79"

logger:

api:
  port: {port}

packages:
  report: !include common/footfall-report.yaml

# Stands in for the Bluetooth scan: the same three devices, over and over.
interval:
  - interval: 100ms
    then:
      - lambda: |-
          footfall::observe(0x{phone:X}ULL, -62, -90);
          footfall::observe(0x{phone:X}ULL, -58, -90);
          footfall::observe(0x{watch:X}ULL, -74, -90);
          footfall::observe(0x{too_weak:X}ULL, -95, -90);
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def node(esphome_bin, tmp_path_factory):
    """Compile the host build once and run it for the tests in this module."""
    workdir = tmp_path_factory.mktemp("host_node")
    shutil.copytree(COMMON, workdir / "common")
    (workdir / "secrets.yaml").write_text(f'footfall_salt: "{SALT}"\n', encoding="utf-8")
    port = _free_port()
    (workdir / "node.yaml").write_text(
        CONFIG.format(name=NODE_NAME, port=port, phone=PHONE, watch=WATCH, too_weak=TOO_WEAK),
        encoding="utf-8",
    )

    build = subprocess.run(
        [esphome_bin, "compile", "node.yaml"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stdout[-4000:] + build.stderr[-4000:]

    programs = list((workdir / ".esphome" / "build" / NODE_NAME).rglob("program"))
    assert programs, "host build produced no program"
    process = subprocess.Popen(
        [str(programs[0])], cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        yield port
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


async def _collect_events(port: int, count: int, timeout: float = 30.0):
    """Connect like Home Assistant does and return the first `count` events."""
    client = aioesphomeapi.APIClient("127.0.0.1", port, password=None)
    deadline = time.monotonic() + timeout
    while True:
        try:
            await client.connect(login=True)
            break
        except aioesphomeapi.APIConnectionError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.2)

    events: list = []
    done = asyncio.Event()

    def on_service_call(call) -> None:
        events.append(call)
        if len(events) >= count:
            done.set()

    try:
        client.subscribe_service_calls(on_service_call)
        await asyncio.wait_for(done.wait(), timeout=max(1.0, deadline - time.monotonic()))
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
    return events


@pytest.fixture(scope="module")
def events(node):
    day_before = int(time.time()) // 86400
    collected = asyncio.run(_collect_events(node, count=3))
    day_after = int(time.time()) // 86400
    return collected, {day_before, day_after}


def test_node_sends_footfall_events(events):
    collected, _ = events
    assert len(collected) >= 3
    for call in collected:
        assert call.is_event
        assert call.service == "esphome.ble_footfall"
        assert set(call.data) == {"node", "devs"}
        assert call.data["node"] == NODE_NAME


def test_event_lists_each_device_once_with_its_strongest_reading(events):
    collected, days = events
    for call in collected:
        report = parse_sightings(call.data["devs"])
        assert sorted(report.values()) == [-74.0, -58.0]
        expected = [
            {anon_id(PHONE, SALT, day): -58.0, anon_id(WATCH, SALT, day): -74.0} for day in days
        ]
        assert report in expected, "ids must be the salted hash for today's date"


def test_readings_below_the_floor_never_leave_the_node(events):
    collected, days = events
    weak_ids = {anon_id(TOO_WEAK, SALT, day) for day in days}
    for call in collected:
        assert not weak_ids & set(parse_sightings(call.data["devs"]))


def test_raw_addresses_never_leave_the_node(events):
    collected, _ = events
    for call in collected:
        text = str(call.data).lower()
        for addr in (PHONE, WATCH):
            assert f"{addr:x}" not in text
            assert f"{addr:012x}" not in text
