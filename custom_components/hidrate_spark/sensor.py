"""The readings."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfVolume

from .coordinator import HidrateCoordinator
from .entity import HidrateEntity


@dataclass(frozen=True, kw_only=True)
class HidrateSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HidrateCoordinator], float | int | datetime | None]
    attrs_fn: Callable[[HidrateCoordinator], dict[str, Any]] | None = None


def _progress(c: HidrateCoordinator) -> int | None:
    return round(c.total_today() / c.goal_ml * 100) if c.goal_ml else None


SENSORS = (
    HidrateSensorDescription(
        key="today",
        name="Today",
        icon="mdi:cup-water",
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.total_today()),
        # A string, so a long-term store keeps it as one field. Sips read late land on the day they
        # were drunk, and this is how anything downstream can see that.
        attrs_fn=lambda c: {"daily_totals": json.dumps(c.log.recent_totals(c.today()))},
    ),
    HidrateSensorDescription(
        key="yesterday",
        name="Yesterday",
        icon="mdi:cup-water",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.total_yesterday()),
    ),
    HidrateSensorDescription(
        key="goal_progress",
        name="Goal progress",
        icon="mdi:target",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_progress,
    ),
    HidrateSensorDescription(
        key="last_sip",
        name="Last sip",
        icon="mdi:water",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.log.last.at if c.log.last else None,
        attrs_fn=lambda c: {"amount_ml": round(c.log.last.ml) if c.log.last else None},
    ),
    HidrateSensorDescription(
        key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.battery,
    ),
    HidrateSensorDescription(
        key="last_sync",
        name="Last sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.last_sync,
        attrs_fn=lambda c: {"last_error": c.last_error},
    ),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(HidrateSensor(coordinator, description) for description in SENSORS)


class HidrateSensor(HidrateEntity, SensorEntity):
    entity_description: HidrateSensorDescription
    # Fourteen days of totals on every state would bloat the recorder; the attribute is still published.
    _unrecorded_attributes = frozenset({"daily_totals"})

    @property
    def native_value(self) -> float | int | datetime | None:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = self.entity_description.attrs_fn
        return attrs(self.coordinator) if attrs else None
