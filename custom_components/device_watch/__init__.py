"""Device Watch - every device that drops off the network or runs low on battery.

The automations this replaces had to be told which entities to watch, and so
forgot every device added after they were written. This reads the device
registry instead: a device is watched because it exists, and a label on its
device page is the only setting it has (see SPEC.md).

The integration decides; it does not notify. It fires events and keeps two
summary sensors, and an automation turns the events into phone notifications
through the household's notification script - so who gets told, on which
channel and how loudly stays where every other notification is decided.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .watcher import DeviceWatcher
from .websocket import async_register

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    watcher = DeviceWatcher(hass, entry)
    await watcher.async_start()
    hass.data[DOMAIN] = watcher

    async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Options apply by reloading: the watcher reads them once, and its records
    # are in the store, so a reload loses nothing.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    watcher: DeviceWatcher | None = hass.data.pop(DOMAIN, None)
    if watcher:
        await watcher.async_stop()
    # The websocket command stays registered, as in Home Events: unregistering
    # is not part of the API, and the handler answers cleanly without a watcher.
    return unloaded
