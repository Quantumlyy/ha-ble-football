"""Tracking engine: merges sightings from all nodes into visits.

Pure Python with no Home Assistant imports, so it can be tested and tuned on
its own. Time is whatever monotonically increasing number of seconds the caller
passes in.

Life of a device:

1. Nodes report "device X at -64 dBm" every couple of seconds. All reports with
   the same id belong to one track, whichever node sent them.
2. While the track is alive, each report updates a position estimate (weighted
   centroid of the node positions, weights from RSSI-derived distance).
3. When nothing has been heard for ``gone_after`` seconds the track is closed
   and classified. Only tracks that came close enough, were confirmed by more
   than one node and did not linger are counted as a visit.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
import math

COUNTED = "counted"
NOISE = "noise"            # heard too few times to mean anything
FAR = "far"                # never came close to any node
OUTSIDE = "outside"        # near one node only, e.g. walking past a glass door
STATIONARY = "stationary"  # stayed longer than max_dwell

REJECT_REASONS = (NOISE, FAR, OUTSIDE, STATIONARY)


def parse_sightings(raw: str) -> dict[str, float]:
    """Parse a node's report, ``"1a2b3c4d:-61;9f8e7d6c:-74;"``, into id -> RSSI.

    The format is produced by footfall::drain() in esphome/common/footfall.h.
    Malformed items are skipped.
    """
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


@dataclass(frozen=True)
class Node:
    """A scanner at a known position (metres, any origin)."""

    name: str
    x: float
    y: float
    door: str | None = None      # label of the door this node sits at, if any
    rssi_offset: float = 0.0     # added to every RSSI from this node (calibration)


@dataclass
class Settings:
    rssi_at_1m: float = -60.0        # RSSI of a typical phone 1 m from a node
    path_loss_exponent: float = 2.7  # 2.0 = open space, 3+ = cluttered indoors
    enter_rssi: float = -70.0        # some node must hear the device this strongly
    # ...and min_nodes different nodes at least this strongly at some point.
    # None derives it from the layout: see Engine._derive_confirm_rssi.
    confirm_rssi: float | None = None
    confirm_reach: float = 0.75      # used for the derived value
    min_nodes: int = 2
    min_hits: int = 3                # minimum node reports for a real device
    gone_after: float = 20.0         # seconds of silence that end a visit
    max_dwell: float = 600.0         # longer than this is furniture, not footfall
    fix_window: float = 5.0          # reports this recent are combined into one fix
    smoothing: float = 0.5           # 0..1, weight of the newest RSSI per node
    edge_window: float = 6.0         # seconds at each end used to pick the doors
    max_tracks: int = 2000


@dataclass(frozen=True)
class Visit:
    started: float
    ended: float
    entry: str | None            # door label, or None if unclear
    exit: str | None
    peak_node: str
    peak_rssi: float
    path: tuple[tuple[float, float], ...]   # estimated positions, oldest first

    @property
    def dwell(self) -> float:
        return self.ended - self.started


@dataclass
class _Track:
    first: float
    last: float
    hits: int = 0
    stationary: bool = False
    peaks: dict[str, float] = field(default_factory=dict)
    recent: dict[str, tuple[float, float]] = field(default_factory=dict)
    head: list[tuple[float, str, float]] = field(default_factory=list)
    tail: deque[tuple[float, str, float]] = field(default_factory=deque)
    fixes: deque[tuple[float, float, float]] = field(
        default_factory=lambda: deque(maxlen=120)
    )


class Engine:
    """Feed it sightings with ingest(), collect finished visits with sweep()."""

    def __init__(self, nodes: list[Node], settings: Settings | None = None) -> None:
        if not nodes:
            raise ValueError("at least one node is required")
        self.nodes: dict[str, Node] = {n.name: n for n in nodes}
        self.settings = settings or Settings()
        self.doors: list[str] = sorted({n.door for n in nodes if n.door})
        self._tracks: dict[str, _Track] = {}
        self.confirm_rssi: float = (
            self.settings.confirm_rssi
            if self.settings.confirm_rssi is not None
            else self._derive_confirm_rssi()
        )

        self.total = 0
        self.entries: dict[str, int] = {d: 0 for d in self.doors}
        self.exits: dict[str, int] = {d: 0 for d in self.doors}
        self.transits: dict[tuple[str, str], int] = {
            (a, b): 0 for a in self.doors for b in self.doors
        }
        self.rejected: dict[str, int] = {r: 0 for r in REJECT_REASONS}

    # ------------------------------------------------------------------ input

    def ingest(self, node_name: str, ts: float, sightings: Mapping[str, float]) -> bool:
        """Record one report from one node. Returns False for an unknown node."""
        node = self.nodes.get(node_name)
        if node is None:
            return False
        s = self.settings
        for dev, raw_rssi in sightings.items():
            rssi = raw_rssi + node.rssi_offset
            track = self._tracks.get(dev)
            if track is None:
                if len(self._tracks) >= s.max_tracks:
                    self._drop_oldest()
                track = self._tracks[dev] = _Track(first=ts, last=ts)
            track.last = max(track.last, ts)
            track.hits += 1
            # Smooth per node before anything else looks at the value: single
            # readings swing by several dB and would otherwise set the peaks.
            previous = track.recent.get(node_name)
            if previous is not None and ts - previous[0] <= s.fix_window:
                rssi = s.smoothing * rssi + (1 - s.smoothing) * previous[1]
            track.recent[node_name] = (ts, rssi)
            if rssi > track.peaks.get(node_name, -999.0):
                track.peaks[node_name] = rssi
            if track.stationary:
                continue
            sample = (ts, node_name, rssi)
            if ts - track.first <= s.edge_window:
                track.head.append(sample)
            track.tail.append(sample)
            while track.tail and track.tail[0][0] < ts - s.edge_window:
                track.tail.popleft()
            fix = self._fix(track, ts)
            if fix is not None:
                track.fixes.append((ts, fix[0], fix[1]))
        return True

    def sweep(self, now: float) -> list[Visit]:
        """Close tracks that went silent. Returns the visits that were counted."""
        s = self.settings
        visits: list[Visit] = []
        for dev, track in list(self._tracks.items()):
            if now - track.last > s.gone_after:
                del self._tracks[dev]
                outcome = self._classify(track)
                if outcome != COUNTED:
                    self.rejected[outcome] += 1
                    continue
                visit = self._to_visit(track)
                self._count(visit)
                visits.append(visit)
            elif not track.stationary and track.last - track.first > s.max_dwell:
                # Stop spending memory on it, but keep the track so that it is
                # not counted again while it remains in range.
                track.stationary = True
                track.head.clear()
                track.tail.clear()
                track.fixes.clear()
        return visits

    # ----------------------------------------------------------------- output

    def present(self) -> list[tuple[float, float]]:
        """Last estimated position of every device that currently qualifies."""
        out: list[tuple[float, float]] = []
        for track in self._tracks.values():
            if track.stationary or not self._qualifies(track) or not track.fixes:
                continue
            _, x, y = track.fixes[-1]
            out.append((round(x, 1), round(y, 1)))
        return out

    @property
    def tracked(self) -> int:
        """All devices in memory, including ones that will never be counted."""
        return len(self._tracks)

    # --------------------------------------------------------------- internals

    def _distance(self, rssi: float) -> float:
        s = self.settings
        d = 10 ** ((s.rssi_at_1m - rssi) / (10 * s.path_loss_exponent))
        return max(d, 0.3)

    def _fix(self, track: _Track, ts: float) -> tuple[float, float] | None:
        """Estimate a position from the nodes that heard the device recently.

        Weighted centroid of the node positions, weighted by 1/distance^2 with
        distance derived from RSSI. A least-squares trilateration reacts badly
        to the 5-10 dB swings a body or a pocket causes; the centroid is cruder
        but cannot produce a wild position, because it always lies between the
        nodes. Positions feed the path and the live map, not the counting.
        """
        wsum = xsum = ysum = 0.0
        heard = 0
        for name, (seen, rssi) in track.recent.items():
            if ts - seen > self.settings.fix_window:
                continue
            node = self.nodes[name]
            w = 1.0 / self._distance(rssi) ** 2
            wsum += w
            xsum += w * node.x
            ysum += w * node.y
            heard += 1
        if heard < 2:
            return None
        return (xsum / wsum, ysum / wsum)

    def _derive_confirm_rssi(self) -> float:
        """Pick confirm_rssi from the node layout.

        The counting rule is "came within reach of at least two different
        nodes". Reach is set to ``confirm_reach`` times the smallest spacing
        between two nodes. Someone among the nodes gets that close to two of
        them; someone beyond one node (walking past a glass door, sitting in
        the next room) is close to that node only.
        """
        s = self.settings
        nodes = list(self.nodes.values())
        spacing = min(
            (
                ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
                for i, a in enumerate(nodes)
                for b in nodes[i + 1 :]
            ),
            default=0.0,
        )
        if spacing <= 0.0:
            return -82.0
        reach = max(s.confirm_reach * spacing, 0.5)
        return s.rssi_at_1m - 10 * s.path_loss_exponent * math.log10(reach)

    def _qualifies(self, track: _Track) -> bool:
        s = self.settings
        if track.hits < s.min_hits or not track.peaks:
            return False
        if max(track.peaks.values()) < s.enter_rssi:
            return False
        confirmed = sum(1 for r in track.peaks.values() if r >= self.confirm_rssi)
        return confirmed >= min(s.min_nodes, len(self.nodes))

    def _classify(self, track: _Track) -> str:
        s = self.settings
        if track.hits < s.min_hits or not track.peaks:
            return NOISE
        if track.stationary or track.last - track.first > s.max_dwell:
            return STATIONARY
        if max(track.peaks.values()) < s.enter_rssi:
            return FAR
        if not self._qualifies(track):
            return OUTSIDE
        return COUNTED

    def _strongest_door(self, samples: list[tuple[float, str, float]]) -> str | None:
        """Door whose node heard the device best in these samples."""
        best: str | None = None
        best_rssi = self.confirm_rssi
        for _, name, rssi in samples:
            door = self.nodes[name].door
            if door is not None and rssi >= best_rssi:
                best, best_rssi = door, rssi
        return best

    def _to_visit(self, track: _Track) -> Visit:
        # Look at the first and last few seconds; for a quick pass-through,
        # split the track in half instead so the two ends do not overlap.
        edge = min(self.settings.edge_window, (track.last - track.first) / 2)
        head = [smp for smp in track.head if smp[0] <= track.first + edge]
        tail = [smp for smp in track.tail if smp[0] >= track.last - edge]
        peak_node = max(track.peaks, key=lambda n: track.peaks[n])
        return Visit(
            started=track.first,
            ended=track.last,
            entry=self._strongest_door(head),
            exit=self._strongest_door(tail),
            peak_node=peak_node,
            peak_rssi=track.peaks[peak_node],
            path=tuple((round(x, 1), round(y, 1)) for _, x, y in track.fixes),
        )

    def _count(self, visit: Visit) -> None:
        self.total += 1
        if visit.entry is not None:
            self.entries[visit.entry] += 1
        if visit.exit is not None:
            self.exits[visit.exit] += 1
        if visit.entry is not None and visit.exit is not None:
            self.transits[(visit.entry, visit.exit)] += 1

    def _drop_oldest(self) -> None:
        oldest = min(self._tracks, key=lambda d: self._tracks[d].last)
        del self._tracks[oldest]
