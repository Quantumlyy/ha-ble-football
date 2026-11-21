"""A small radio simulator: people walking past four nodes.

RSSI follows the same log-distance model the engine assumes, optionally with
Gaussian noise. This checks the logic, not real-world accuracy.
"""

from __future__ import annotations

import math
import random

from custom_components.ble_footfall.tracker import Engine, Node, Settings

# 8 m x 4 m space: doors on the short ends, side nodes on the long walls.
NODES = [
    Node("door-a", 0.0, 2.0, door="A"),
    Node("door-b", 8.0, 2.0, door="B"),
    Node("side-c", 4.0, 4.0),
    Node("side-d", 4.0, 0.0),
]

REPORT_INTERVAL = 2.0
RSSI_FLOOR = -90.0
WALK_SPEED = 1.4

THROUGH_A_TO_B = [(-6.0, 2.0), (14.0, 2.0)]
THROUGH_B_TO_A = [(14.0, 2.0), (-6.0, 2.0)]
IN_AND_OUT_A = [(-6.0, 2.0), (3.5, 2.0), (-6.0, 2.0)]
PAST_DOOR_A_OUTSIDE = [(-1.5, -10.0), (-1.5, 14.0)]
PAST_SIDE_C_OUTSIDE = [(-8.0, 5.5), (16.0, 5.5)]


def make_engine(**overrides) -> Engine:
    return Engine(NODES, Settings(**overrides))


def position_at(path, t, speed=WALK_SPEED):
    """Position after walking t seconds along a polyline, or None once done."""
    remaining = t * speed
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        leg = math.hypot(x1 - x0, y1 - y0)
        if remaining <= leg:
            f = remaining / leg if leg else 0.0
            return (x0 + f * (x1 - x0), y0 + f * (y1 - y0))
        remaining -= leg
    return None


def is_inside(pos) -> bool:
    return 0.0 <= pos[0] <= 8.0 and 0.0 <= pos[1] <= 4.0


def rssi_at(
    node: Node, pos, settings: Settings, rng=None, noise=0.0, wall_loss=0.0
) -> float:
    d = max(math.hypot(pos[0] - node.x, pos[1] - node.y), 0.5)
    rssi = settings.rssi_at_1m - 10 * settings.path_loss_exponent * math.log10(d)
    if not is_inside(pos):
        rssi -= wall_loss
    if rng is not None and noise:
        rssi += rng.gauss(0.0, noise)
    return rssi


def run(engine: Engine, walkers, until, rng=None, noise=0.0, wall_loss=0.0):
    """Simulate walkers = {dev: (start_time, path)} and return counted visits.

    Each node reports every REPORT_INTERVAL seconds, out of phase with the
    others, with the strongest RSSI it saw in that interval. wall_loss is the
    extra attenuation in dB for anyone outside the 8 m x 4 m space.
    """
    visits = []
    step = 0.5
    best: dict[tuple[str, str], float] = {}
    t = 0.0
    tick = 0
    while t <= until:
        for dev, (start, path) in walkers.items():
            if t < start:
                continue
            pos = path(t - start) if callable(path) else position_at(path, t - start)
            if pos is None:
                continue
            for node in engine.nodes.values():
                rssi = rssi_at(node, pos, engine.settings, rng, noise, wall_loss)
                if rssi >= RSSI_FLOOR:
                    key = (node.name, dev)
                    best[key] = max(best.get(key, -999.0), rssi)
        for i, node in enumerate(engine.nodes.values()):
            if (tick - i) % int(REPORT_INTERVAL / step) == 0:
                report = {
                    dev: round(rssi)
                    for (name, dev), rssi in best.items()
                    if name == node.name
                }
                for dev in report:
                    del best[(node.name, dev)]
                if report:
                    engine.ingest(node.name, t, report)
        visits.extend(engine.sweep(t))
        t += step
        tick += 1
    return visits


def standing_at(x, y, seconds):
    return lambda t: (x, y) if t <= seconds else None
