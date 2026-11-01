"""The two summary sensors: offline devices and low batteries.

State is the number of reported devices; the attributes carry the list, and a
ready-made title and detail so the Events card can show "Kitchen | Motion" for
one device and "3 devices offline" for several without a template of its own.
Only reported devices count - a device in its first fifteen minutes offline is
not on the list yet, the same as it is not on anybody's phone yet.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_NAME, DOMAIN, SIGNAL_UPDATED, VERSION
from .watcher import DeviceWatcher


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    watcher: DeviceWatcher = hass.data[DOMAIN]
    async_add_entities([OfflineSensor(entry, watcher), BatterySensor(entry, watcher)])


def _level(value: float | None) -> str:
    return f"{round(value)} %" if value is not None else ""


class _Summary(SensorEntity):
    # Named in full, like every other entity here ("System | Device Watch |
    # Offline"): with has_entity_name the device name would be glued on with a
    # space instead of a bar.
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, watcher: DeviceWatcher) -> None:
        self._watcher = watcher
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEVICE_NAME,
            entry_type=DeviceEntryType.SERVICE,
            sw_version=VERSION,
        )
        self._devices: list[dict[str, Any]] = []

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_UPDATED, self._update))
        self._update()

    @callback
    def _update(self) -> None:
        self._devices = sorted(self._read(), key=lambda d: d["name"].lower())
        if self.hass and self.entity_id:
            self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._devices)


class OfflineSensor(_Summary):
    _key = "offline"
    _attr_name = "System | Device Watch | Offline"
    _attr_icon = "mdi:lan-disconnect"

    def _read(self) -> list[dict[str, Any]]:
        return self._watcher.offline_devices()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        names = ", ".join(d["name"] for d in self._devices)
        many = len(self._devices) > 1
        return {
            "devices": self._devices,
            "names": names,
            "title": f"{len(self._devices)} devices offline" if many else names,
            "detail": names if many else "",
        }


class BatterySensor(_Summary):
    _key = "battery_low"
    _attr_name = "System | Device Watch | Battery | Low"
    _attr_icon = "mdi:battery-alert-variant-outline"

    def _read(self) -> list[dict[str, Any]]:
        return self._watcher.battery_devices()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        names = ", ".join(d["name"] for d in self._devices)
        many = len(self._devices) > 1
        one = self._devices[0] if len(self._devices) == 1 else None
        return {
            "devices": self._devices,
            "names": names,
            "title": f"{len(self._devices)} low batteries" if many else names,
            "detail": names if many else (_level(one["level"]) if one else ""),
        }
