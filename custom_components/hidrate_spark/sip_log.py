"""The record of sips, which replaces the app's. No Home Assistant in here, so it can be tested alone."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .protocol import Packet, amount_ml

DEDUPE_SECONDS = 120
KEEP_SIPS_DAYS = 90


@dataclass(frozen=True, order=True)
class Sip:
    at: datetime
    ml: float
    raw: tuple[int, int, int, int]


class SipLog:
    """Sips for 90 days, daily totals for good. Both are saved, so a restart loses nothing."""

    def __init__(self, tz: tzinfo) -> None:
        self._tz = tz
        self.sips: list[Sip] = []
        self.daily: dict[str, float] = {}

    def add(self, packet: Packet, at: datetime, size_ml: float) -> Sip | None:
        """Returns the sip, or None for a refill (the level rose) or a repeat of one already held."""
        ml = amount_ml(packet, size_ml)
        if ml <= 0:
            return None
        window = timedelta(seconds=DEDUPE_SECONDS)
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

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_SIPS_DAYS)
        self.sips = [s for s in self.sips if s.at >= cutoff]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sips": [[s.at.isoformat(), s.ml, *s.raw] for s in self.sips],
            "daily": self.daily,
        }

    def load(self, data: dict[str, Any]) -> None:
        self.sips = sorted(
            Sip(datetime.fromisoformat(row[0]), row[1], tuple(row[2:6]))
            for row in data.get("sips", [])
        )
        self.daily = dict(data.get("daily", {}))
