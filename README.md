<img src="brand/icon.png" alt="Device Watch" width="128" align="right">

# Device Watch

A Home Assistant integration that watches **every physical device** for going offline
and for a low battery. It reports both by the device's own name.

It reads the device registry instead of a list you keep up to date, so a device added
tomorrow is watched tomorrow. Device Watch decides *what* counts and *when*; it fires
events and keeps two summary sensors. How you hear about it (phone notification,
dashboard, anything else) is up to an automation of your own.

## Installation

1. In HACS, open the menu → **Custom repositories**, and add
   `https://github.com/Sandolution/ha-device-watch` with the type **Integration**.
2. Install **Device Watch** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and pick **Device Watch**.
   There is nothing to fill in.

## What counts

**Watched devices:** every device in the registry, except:

- service devices, such as integrations that show up as a device;
- disabled devices;
- devices labelled **Device Watch | Unwatched**.

**Offline:** a device is offline when **all of its own entities** are `unavailable`.

- *Own* means coming from the device's own integration. Entities that other
  integrations attach to a device (Powercalc, utility_meter, …) keep their state when the
  device is gone, so they never count.
- On a **parent** device (one that others name as their `via_device`, such as the
  Zigbee2MQTT bridge), a `connectivity` binary sensor reading `off` also counts.

**When an offline device is reported:**

- after 15 minutes, or after 5 minutes when the device is labelled **Device Watch |
  Critical**;
- not while its parent device is offline. After the parent comes back, the device gets
  its own delay again;
- devices that reach their delay within 2 minutes of each other are reported together,
  as one group;
- a reported device is "back" as soon as one of its own entities has a state again.

**Low battery:**

- A low battery is any `battery` entity on the device below 20 % for an hour. A
  `binary_sensor` of class `battery` counts when it reads `on`.
- It is cleared above 30 %: a new battery, not a reading wobbling around 20 %.
- The battery isn't judged while the device is offline.
- Devices labelled **Device Watch | No Battery Alerts** are skipped for this.

**Restarts:** what has been reported is kept across restarts, so a restart neither
repeats a report nor forgets one. Nothing is judged in the first 5 minutes after Home
Assistant starts, while MQTT devices are still reconnecting.

## Settings

**Per device:** labels on the device page. Device Watch creates these labels on startup
if they don't exist yet.

| Label | Effect |
|---|---|
| Device Watch \| Unwatched | The device is ignored completely. It still counts as a parent for the devices under it. |
| Device Watch \| Critical | Offline after the Critical delay instead of the normal one, and marked `critical: true` in the offline event. |
| Device Watch \| No Battery Alerts | The device is watched for going offline; its battery is not. |

A device without any of these labels is watched for both, with the normal delay.

**Thresholds:** under **Configure** on the integration. Changes apply without a restart.
Adding the integration asks nothing; these are its only options.

| Option | Values | Default | What it does |
|---|---|---|---|
| Offline after | 1 to 1440 minutes | 15 | How long every own entity of a device has to be unavailable before it is reported. |
| Offline after (Critical) | 1 to 1440 minutes | 5 | The same, for devices labelled Device Watch \| Critical. |
| Battery low below | 1 to 99 % | 20 | A battery level below this counts as low. |
| Battery low for | 0 to 1440 minutes | 60 | How long it has to stay low before it is reported. `0` reports it at once. |
| Battery cleared above | 2 to 100 % | 30 | A reported battery is cleared once its level is above this. Has to be higher than *Battery low below*. |
| Group window | 0 to 60 minutes | 2 | Devices that reach their delay within this window of the first one are reported together. With `0`, only devices that reach their delay in the same half minute are grouped. |

## Sensors

| Entity | State | Attributes |
|---|---|---|
| `sensor.system_device_watch_offline` | number of reported offline devices | `devices`, `names`, `title`, `detail` |
| `sensor.system_device_watch_battery_low` | number of reported low batteries | `devices`, `names`, `title`, `detail` |

`title` is the device name for one device, and "3 devices offline" or "3 low
batteries" for several. `detail` lists the names when there are several, or the battery
level for one. Both are ready to use on a dashboard.

## Events

| Event | Data |
|---|---|
| `device_watch_offline` | `group_id`, `devices` (list), `count`, `critical` |
| `device_watch_online` | `group_id`, `device` (the one that is back), `remaining` (still offline from that group) |
| `device_watch_battery_low` | `device` |
| `device_watch_battery_ok` | `device` |

**Device fields:**
- offline and online devices: `device_id`, `name`, `area`, `since`, `critical`,
  `battery_level`;
- battery devices: `device_id`, `name`, `area`, `since`, `entity_id`, `level`.

### Example automation

A phone notification per group that is updated as devices come back. Replace
`notify.mobile_app_phone` with your own notify action.

```yaml
- alias: Device Watch - Notify
  triggers:
    - trigger: event
      event_type: device_watch_offline
      id: offline
    - trigger: event
      event_type: device_watch_online
      id: online
    - trigger: event
      event_type: device_watch_battery_low
      id: battery_low
  actions:
    - variables:
        data: "{{ trigger.event.data }}"
        offline: "{{ data.devices if trigger.id == 'offline' else data.remaining | default([]) }}"
    - choose:
        - conditions: "{{ trigger.id in ['offline', 'online'] and offline | count > 0 }}"
          sequence:
            - action: notify.mobile_app_phone
              data:
                message: >-
                  {% if offline | count == 1 %}{{ offline[0].name }} is offline.
                  {% else %}{{ offline | count }} devices are offline:
                  {{ offline | map(attribute='name') | join(', ') }}.{% endif %}
                data:
                  tag: "device_watch_{{ data.group_id }}"
        - conditions: "{{ trigger.id == 'online' }}"
          sequence:
            - action: notify.mobile_app_phone
              data:
                message: clear_notification
                data:
                  tag: "device_watch_{{ data.group_id }}"
        - conditions: "{{ trigger.id == 'battery_low' }}"
          sequence:
            - action: notify.mobile_app_phone
              data:
                message: "{{ data.device.name }} battery level is at {{ data.device.level | round(0) | int }} %."
  mode: queued
```

## Checking it

The WebSocket command `device_watch/status` (admin only) lists every device Device
Watch considered:

- whether it is watched, and if not, why;
- whether it is offline, since when, and whether that was reported;
- its battery entity and level.

It is the first thing to look at when a report did or did not arrive.

## Background

[SPEC.md](SPEC.md) is the specification the integration was built from, with the
reasoning behind each rule.

## License

[MIT](LICENSE)
