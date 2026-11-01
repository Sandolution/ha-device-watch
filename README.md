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
   `https://github.com/sandolution/device-watch` with the type **Integration**.
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
| Device Watch \| Unwatched | Ignored completely; still used as a parent |
| Device Watch \| Critical | Offline after the Critical delay; marked `critical` in events |
| Device Watch \| No Battery Alerts | Offline is watched, the battery is not |

**Thresholds:** under **Configure** on the integration. Changes apply without a restart.

| Option | Default |
|---|---|
| Offline after | 15 min |
| Offline after (Critical) | 5 min |
| Battery low below | 20 % |
| Battery low for | 60 min |
| Battery cleared above | 30 % |
| Group window | 2 min |

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
