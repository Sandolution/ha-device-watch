"""Adding Device Watch, and its settings.

Adding it asks nothing: every default is what the household chose. Configure
has two sections. Devices shows which devices carry each of the three labels
and changes them; the labels stay the only place that setting lives, so the
dialog and a device's own page always agree. Thresholds are the options, for
when a default turns out wrong in practice.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (ConfigEntry, ConfigFlow,
                                          ConfigFlowResult, OptionsFlow)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.selector import (DeviceSelector,
                                            DeviceSelectorConfig,
                                            NumberSelector,
                                            NumberSelectorConfig,
                                            NumberSelectorMode)

from .const import (CONF_BATTERY_CLEAR, CONF_BATTERY_LOW, CONF_BATTERY_MINUTES,
                    CONF_CRITICAL_MINUTES, CONF_GROUP_MINUTES,
                    CONF_OFFLINE_MINUTES, DEFAULTS, DEVICE_NAME, DOMAIN,
                    LABEL_CRITICAL, LABEL_NO_BATTERY, LABEL_UNWATCHED, LABELS)


def _number(low: int, high: int, unit: str) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(
        min=low, max=high, step=1, unit_of_measurement=unit,
        mode=NumberSelectorMode.BOX))


THRESHOLDS = vol.Schema({
    vol.Required(CONF_OFFLINE_MINUTES): _number(1, 1440, "min"),
    vol.Required(CONF_CRITICAL_MINUTES): _number(1, 1440, "min"),
    vol.Required(CONF_BATTERY_LOW): _number(1, 99, "%"),
    vol.Required(CONF_BATTERY_MINUTES): _number(0, 1440, "min"),
    vol.Required(CONF_BATTERY_CLEAR): _number(2, 100, "%"),
    vol.Required(CONF_GROUP_MINUTES): _number(0, 60, "min"),
})

# The field each label is shown under, in the Devices section.
LABEL_FIELDS = {
    "unwatched": LABEL_UNWATCHED,
    "critical": LABEL_CRITICAL,
    "no_battery": LABEL_NO_BATTERY,
}

_DEVICES = DeviceSelector(DeviceSelectorConfig(multiple=True))

OPTIONS_SCHEMA = vol.Schema({
    vol.Required("devices"): section(
        vol.Schema({vol.Optional(field): _DEVICES for field in LABEL_FIELDS}),
        {"collapsed": False}),
    vol.Required("thresholds"): section(THRESHOLDS, {"collapsed": False}),
})


def _labelled(hass: HomeAssistant) -> dict[str, list[str]]:
    """The devices that carry each label now, by field."""
    labels = lr.async_get(hass)
    devices = dr.async_get(hass)
    out: dict[str, list[str]] = {}
    for field, name in LABEL_FIELDS.items():
        label = labels.async_get_label_by_name(name)
        out[field] = [d.id for d in dr.async_entries_for_label(devices, label.label_id)] \
            if label else []
    return out


@callback
def _apply_labels(hass: HomeAssistant, chosen: dict[str, list[str]]) -> None:
    """Give each label to exactly the devices chosen for it.

    A label somebody deleted is created again only when devices are chosen for
    it here: choosing them is asking for it.
    """
    labels = lr.async_get(hass)
    devices = dr.async_get(hass)
    for field, name in LABEL_FIELDS.items():
        wanted = set(chosen.get(field) or [])
        label = labels.async_get_label_by_name(name)
        if label is None:
            if not wanted:
                continue
            icon, description = LABELS[name]
            label = labels.async_create(name, icon=icon, description=description)
        have = {d.id for d in dr.async_entries_for_label(devices, label.label_id)}
        for device_id in wanted ^ have:
            device = devices.async_get(device_id)
            if device is None:
                continue
            new = set(device.labels)
            if device_id in wanted:
                new.add(label.label_id)
            else:
                new.discard(label.label_id)
            devices.async_update_device(device_id, labels=new)


class DeviceWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title=DEVICE_NAME, data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DeviceWatchOptionsFlow()


class DeviceWatchOptionsFlow(OptionsFlow):
    """The labelled devices and the thresholds."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            values = {key: int(value)
                      for key, value in user_input.get("thresholds", {}).items()}
            # Cleared has to be above low, or a battery could be low and
            # cleared at once.
            if values[CONF_BATTERY_CLEAR] <= values[CONF_BATTERY_LOW]:
                errors["base"] = "clear_not_above_low"
            else:
                _apply_labels(self.hass, user_input.get("devices", {}))
                return self.async_create_entry(data=values)
        current = {
            "devices": (user_input or {}).get("devices") or _labelled(self.hass),
            "thresholds": {**DEFAULTS, **self.config_entry.options,
                           **((user_input or {}).get("thresholds") or {})},
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
            errors=errors,
        )
