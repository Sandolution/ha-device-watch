"""One command, for checking the watcher against what is really in the house.

`device_watch/status` lists every device the watcher considered, watched or
not and why, with what it currently believes about it. It is how the
integration was checked before anything acted on it, and the first thing to
look at when a notification did or did not arrive.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_status)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/status"})
@callback
def ws_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    watcher = hass.data.get(DOMAIN)
    if watcher is None:
        connection.send_error(msg["id"], "not_loaded", "Device Watch is not set up.")
        return
    connection.send_result(msg["id"], {
        "ready": watcher.ready,
        "options": watcher.options,
        "devices": watcher.status(),
    })
