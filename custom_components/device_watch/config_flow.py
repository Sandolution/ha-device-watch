"""Adding Device Watch, and its thresholds.

Adding it asks nothing: every default is what the household chose, and the
per-device settings are labels, not options. The options are the thresholds,
for when a default turns out wrong in practice.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (ConfigEntry, ConfigFlow,
                                          ConfigFlowResult, OptionsFlow)
from homeassistant.core import callback
from homeassistant.helpers.selector import (NumberSelector,
                                            NumberSelectorConfig,
                                            NumberSelectorMode)

from .const import (CONF_BATTERY_CLEAR, CONF_BATTERY_LOW, CONF_BATTERY_MINUTES,
                    CONF_CRITICAL_MINUTES, CONF_GROUP_MINUTES,
                    CONF_OFFLINE_MINUTES, DEFAULTS, DEVICE_NAME, DOMAIN)


def _number(low: int, high: int, unit: str) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(
        min=low, max=high, step=1, unit_of_measurement=unit,
        mode=NumberSelectorMode.BOX))


OPTIONS_SCHEMA = vol.Schema({
    vol.Required(CONF_OFFLINE_MINUTES): _number(1, 1440, "min"),
    vol.Required(CONF_CRITICAL_MINUTES): _number(1, 1440, "min"),
    vol.Required(CONF_BATTERY_LOW): _number(1, 99, "%"),
    vol.Required(CONF_BATTERY_MINUTES): _number(0, 1440, "min"),
    vol.Required(CONF_BATTERY_CLEAR): _number(2, 100, "%"),
    vol.Required(CONF_GROUP_MINUTES): _number(0, 60, "min"),
})


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
    """The thresholds."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            values = {key: int(value) for key, value in user_input.items()}
            # Cleared has to be above low, or a battery could be low and
            # cleared at once.
            if values[CONF_BATTERY_CLEAR] <= values[CONF_BATTERY_LOW]:
                errors[CONF_BATTERY_CLEAR] = "clear_not_above_low"
            else:
                return self.async_create_entry(data=values)
        current = {**DEFAULTS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
            errors=errors,
        )
