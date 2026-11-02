"""Constants for Device Watch."""

DOMAIN = "device_watch"
VERSION = "0.2.0"

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

SIGNAL_UPDATED = f"{DOMAIN}_updated"

DEVICE_NAME = "System | Device Watch"

# Bus events. The notification automation listens for these; the payloads are
# described in SPEC.md section 5.
EVENT_OFFLINE = "device_watch_offline"
EVENT_ONLINE = "device_watch_online"
EVENT_BATTERY_LOW = "device_watch_battery_low"
EVENT_BATTERY_OK = "device_watch_battery_ok"

# Labels are found by name, not by id: the id is derived from the name when the
# label is created, and somebody creating it by hand first would get the same
# name but could get a different id.
LABEL_UNWATCHED = "Device Watch | Unwatched"
LABEL_CRITICAL = "Device Watch | Critical"
LABEL_NO_BATTERY = "Device Watch | No Battery Alerts"
LABELS = {
    LABEL_UNWATCHED: ("mdi:eye-off-outline",
                      "Device Watch ignores this device. It still counts as a parent."),
    LABEL_CRITICAL: ("mdi:alert-octagon-outline",
                     "Device Watch reports this device offline sooner, with high priority."),
    LABEL_NO_BATTERY: ("mdi:battery-off-outline",
                       "Device Watch watches this device for offline, not its battery."),
}

CONF_OFFLINE_MINUTES = "offline_minutes"
CONF_CRITICAL_MINUTES = "critical_minutes"
CONF_BATTERY_LOW = "battery_low"
CONF_BATTERY_MINUTES = "battery_minutes"
CONF_BATTERY_CLEAR = "battery_clear"
CONF_GROUP_MINUTES = "group_minutes"

DEFAULTS = {
    CONF_OFFLINE_MINUTES: 15,
    CONF_CRITICAL_MINUTES: 5,
    CONF_BATTERY_LOW: 20,
    CONF_BATTERY_MINUTES: 60,
    CONF_BATTERY_CLEAR: 30,
    CONF_GROUP_MINUTES: 2,
}

# Not an option: this is how long MQTT entities take to come back from
# "unavailable" after a restart, not a matter of taste.
STARTUP_GRACE_SECONDS = 5 * 60

# Thresholds are checked on a clock rather than with a timer per device. Half a
# minute late on a fifteen-minute delay is nothing, and one clock is one thing
# to cancel on unload instead of a hundred.
TICK_SECONDS = 30

# Registry changes come in bursts (a device joining brings a dozen entities
# one by one), so the index is rebuilt once the burst is over.
REBUILD_DELAY_SECONDS = 10
