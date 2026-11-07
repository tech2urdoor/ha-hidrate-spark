"""The record of sips, which replaces the app's. No Home Assistant in here, so it can be tested alone."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .protocol import Packet, amount_ml, level_share

DEDUPE_SECONDS = 120
KEEP_SIPS_DAYS = 90
# A rise smaller than this share of the bottle isn't called a refill. Between sips with no refill,
# the one bottle tested read a rise of exactly nothing, so this is a margin rather than a measurement.
REFILL_MIN_SHARE = 0.05


@dataclass(frozen=True, order=True)
class Sip:
    at: datetime
    ml: float
    raw: tuple[int, int, int, int]


@dataclass(frozen=True)
class Level:
    """How full the bottle read at the end of the latest record."""

    at: datetime
    share: float


@dataclass(frozen=True)
class Refill:
    """exact is False when the rise was only noticed at the next sip, so it happened by then."""

    at: datetime
    share: float
    exact: bool


class SipLog:
    """Sips for 90 days, daily totals for good. Both are saved, so a restart loses nothing."""

    def __init__(self, tz: tzinfo) -> None:
        self._tz = tz
        self.sips: list[Sip] = []
        self.daily: dict[str, float] = {}
        # Records whose level rose. They aren't sips, but they do say how full the bottle is.
        self.rises: list[tuple[datetime, tuple[int, int, int, int]]] = []

    def add(self, packet: Packet, at: datetime, size_ml: float) -> Sip | None:
        """Returns the sip, or None for a refill (the level rose) or a repeat of one already held."""
        ml = amount_ml(packet, size_ml)
        window = timedelta(seconds=DEDUPE_SECONDS)
        if ml < 0:
            if not any(r == packet.raw_key and abs(a - at) <= window for a, r in self.rises):
                bisect.insort(self.rises, (at, packet.raw_key))
            return None
        if ml == 0:
            return None
        if any(s.raw == packet.raw_key and abs(s.at - at) <= window for s in self.sips):
            return None
        sip = Sip(at, round(ml, 1), packet.raw_key)
        bisect.insort(self.sips, sip)
        day = at.astimezone(self._tz).date().isoformat()
        self.daily[day] = round(self.daily.get(day, 0.0) + sip.ml, 1)
        return sip

    def total_for(self, day: date) -> float:
        return self.daily.get(day.isoformat(), 0.0)

    def recent_totals(self, today: date, days: int = 14) -> dict[str, int]:
        """Late sips land on the day they were drunk, so history should come from these, not today's total."""
        first = (today - timedelta(days=days - 1)).isoformat()
        return {d: round(ml) for d, ml in sorted(self.daily.items()) if d >= first}

    @property
    def last(self) -> Sip | None:
        return self.sips[-1] if self.sips else None

    def _records(self) -> list[tuple[datetime, tuple[int, int, int, int], bool]]:
        """Sips and rises in the order they happened, whatever order they were read in."""
        records = [(s.at, s.raw, True) for s in self.sips] + [(a, r, False) for a, r in self.rises]
        return sorted(records, key=lambda record: record[0])

    def level(self) -> Level | None:
        """None until there's a record. A refill since the latest record doesn't show until the next."""
        records = self._records()
        if not records:
            return None
        at, (low, high, _start, stop), _is_sip = records[-1]
        return Level(at, level_share(stop, low, high))

    def refills(self) -> list[Refill]:
        """A record whose level rose, or a sip that starts higher than the one before it stopped."""
        found = []
        previous = None
        for at, (low, high, start, stop), is_sip in self._records():
            first = level_share(start, low, high)
            last = level_share(stop, low, high)
            if not is_sip:
                risen = last - first
            elif previous is not None:
                risen = first - previous
            else:
                risen = 0.0
            if risen >= REFILL_MIN_SHARE:
                found.append(Refill(at, risen, not is_sip))
            previous = last
        return found

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_SIPS_DAYS)
        self.sips = [s for s in self.sips if s.at >= cutoff]
        self.rises = [r for r in self.rises if r[0] >= cutoff]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sips": [[s.at.isoformat(), s.ml, *s.raw] for s in self.sips],
            "daily": self.daily,
            "rises": [[a.isoformat(), *r] for a, r in self.rises],
        }

    def load(self, data: dict[str, Any]) -> None:
        self.sips = sorted(
            Sip(datetime.fromisoformat(row[0]), row[1], tuple(row[2:6]))
            for row in data.get("sips", [])
        )
        self.daily = dict(data.get("daily", {}))
        self.rises = sorted(
            (datetime.fromisoformat(row[0]), tuple(row[1:5])) for row in data.get("rises", [])
        )
