# Device Watch specification (draft 1, 1 Nov 2026)

Device Watch keeps an eye on every physical device in Home Assistant. It notices when a
device drops off the network or its battery runs low, and says so by the device's own
name. It replaces the two fixed-list automations (`* | Battery | Level ...` and
`* | Device - T# Unavailable ...`), which forget every device added after they were
written.

## 1. Scope

A device is watched when all of these hold:

- it is in the device registry and not disabled;
- it is not a service device (`entry_type: service`, e.g. Frank Energie, Sun, Watchman);
- it has at least one **own entity**: an entity that is enabled, has a state, and comes
  from the device's own integration (`entity.config_entry_id == device.config_entry_id`, formerly `primary_config_entry`).
  Entities that other integrations hang on the device, such as the 336 Powercalc and 76
  utility_meter entities, never count. They keep their state when the device is gone.
- it does not have the label **Device Watch | Unwatched**.

New devices are picked up as soon as they appear in the registry. Nothing is kept in a
list.

## 2. Offline

- A device is **offline** when every own entity is `unavailable`. On a **parent**
  device (one that other devices name as their `via_device`), an own `binary_sensor`
  of device class `connectivity` that reads `off` also counts as unavailable. That
  covers the Zigbee2MQTT bridge, whose only entity is such a sensor. The rule is only
  for parents because elsewhere such a sensor is off by design, e.g. Sander's phone's
  Android Auto connection.
- It is **reported** after it has been offline for 15 minutes, or 5 minutes with the
  label **Device Watch | Critical**.
- **Offline since** is the moment the last own entity became unavailable. It is kept in
  the integration's store, so a restart neither restarts the clock nor reports the
  device again.
- **Startup:** for the first 5 minutes after Home Assistant starts nothing is
  evaluated, because MQTT entities come up unavailable until their availability
  messages arrive. After that:
  - a device that was reported before the restart and is still offline stays reported,
    without a new notification;
  - a device that was online before the restart and is offline now gets "offline since
    startup".
- **Back:** a reported device is back as soon as one own entity is no longer
  `unavailable`. Its notification is then taken back.
- **Parents:** a device whose parent (`via_device`) is offline is not reported. The
  parent is: Zigbee2MQTT for the 79 Zigbee devices, and Frigate for the 8 camera
  devices. The parent's own guard reports the outage once. When the parent comes
  back, children that are still offline after their own delay are reported as usual.
- **Grouping:** devices that reach their delay within 2 minutes of each other are sent
  as one notification, e.g. "5 devices are offline: Garden | Wall | Back, ...". This
  catches outages without a known parent, such as an MQTT broker restart, a Wi-Fi
  access point or a garden circuit. A single device is sent on its own.

## 3. Battery

- A **battery entity** is any entity on the device, own or not, with device class
  `battery`:
  - a numeric `sensor` in %;
  - a `binary_sensor`, where `on` means low.
- **Low:** below 20 % for 1 hour, or a binary sensor `on` for 1 hour.
- **Cleared:** above 30 %, or the binary sensor `off`. That means a new battery, not a
  reading wobbling around 20 %.
- **While offline:** the battery is not judged while the device is offline. The last
  level is kept and shown in the offline notification.
- **Restarts:** the reported state is stored, so a restart doesn't report again.
- The label **Device Watch | No Battery Alerts** turns battery checks off for a device
  while keeping the offline check (e.g. the robot vacuums, which recharge on their
  dock).
- Low batteries are sent one per device and are never grouped.

## 4. Settings

**Options:** Settings > Devices & services > Device Watch > Configure. Changes apply at
once, without a restart.

| Option | Default |
|---|---|
| Offline after | 15 min |
| Offline after (Critical) | 5 min |
| Battery low below | 20 % |
| Battery low for | 60 min |
| Battery cleared above | 30 % |
| Group window | 2 min |

**Labels:** the integration creates these labels if they are missing. The options
dialog shows them as three device lists (Not watched, Critical, No battery alerts) next
to the thresholds; saving it gives and takes away the labels, so the labels stay the
only place the setting lives.

| Label | Effect |
|---|---|
| Device Watch \| Unwatched | Device is ignored completely. It is still used as a parent. |
| Device Watch \| Critical | Offline after the Critical delay; notification with high priority. |
| Device Watch \| No Battery Alerts | Offline is watched, the battery is not. |

## 5. What it creates

The integration is a single config entry, set up through the UI. It adds a service
device **System | Device Watch** with two sensors.

**`sensor.system_device_watch_offline`** (*System | Device Watch | Offline*)

- State: the number of reported offline devices.
- Attributes:
  - `devices`: a list of `{device_id, name, area, since, critical, battery_level}`;
  - `names`: "A, B, C";
  - `title`: the device name for one device, or "N devices offline";
  - `detail`: an empty string for one device, otherwise `names`.

**`sensor.system_device_watch_battery_low`** (*System | Device Watch | Battery | Low*)

- State: the number of devices with a reported low battery.
- Attributes:
  - `devices`: a list of `{device_id, name, area, since, entity_id, level}`;
  - `names`, `title` and `detail`: as above. A single device's title reads
    "Kitchen | Motion", with the detail "18 %".

**Events on the event bus.** The notification automation listens for these.

| Event | Data |
|---|---|
| `device_watch_offline` | `group_id`, `devices` (as in the attribute), `count`, `critical` (true when any device in it is Critical) |
| `device_watch_online` | `group_id`, `device` (the one that is back), `remaining` (the devices of that group still offline) |
| `device_watch_battery_low` | `device` (`{device_id, name, entity_id, level}`) |
| `device_watch_battery_ok` | `device` |

**WebSocket.** `device_watch/status` lists every device it considered: watched or
skipped (and why), offline since, battery entity and level, labels. It is meant for
checking the integration before trusting it, and for debugging later.

## 6. Notifications and the Events card (outside the integration)

**One automation** in automations.yaml:
`* | Device Watch - T# Offline / Online / Battery Low / OK - A# Notify (Sander) / Clear`.

- It sends through `script.system_notifications_a_send_via_ha_companion` to `sverbeek`
  on the channel **Devices**.
- Tags: offline uses one tag per `group_id`, battery uses one tag per device.
- Critical offline notifications go out with high priority.
- Messages:
  - "**Kitchen | Motion** is offline. Its battery level was 18 %." (the last sentence
    only for a device with a battery)
  - "**5 devices** are offline: A, B, C, D, E."
  - "**Kitchen | Motion** battery level is at 18 %."
- **When a device comes back:**
  - a single device's notification is deleted;
  - a group's notification is updated without a sound, listing the devices still
    offline, and deleted once none is left.

**Events card:** the entries "Devices - Offline" and "Devices - Battery (Low)" are
replaced by entries on the two sensors:
- they show when the state is above 0;
- the title is `{attr.title}` and the detail `{attr.detail}`.

## 7. Migration (in the same batch, once it runs)

**Remove:**
- the automations `* | Battery | Level - T# Below 20% ...` and `* | Device - T#
  Unavailable (15m) ...`;
- the "unavailable" half of the always-on plug guard, which keeps "switched off";
- the "offline" half of the litter box rule, which keeps "stuck".

**Keep:** the Zigbee2MQTT guard and the Frigate camera guard. A camera without a
picture is not an offline device, because its switches and sensors stay up.

**Labels:**

- **Critical:** the 11 always-on plugs (fridge, freezer, dishwasher, oven, water
  heater, washer, dryer, boiler, server, air conditioner, irrigation pump). The litter
  box stays on the normal 15 minutes (its rule used 30).
- **Unwatched:**
  - the wall tablets (Fully Kiosk and Browser Mod);
  - the Christmas tree;
  - the ice maker's own module, which goes dark whenever its plug is off;
  - the Zigbee2MQTT bridge and the Frigate server, which have their own guards. They
    are still used as parents.
- **No Battery Alerts:** both robot vacuums.

## 8. Build and rollout

1. **Code:** its own repository (`Sandolution/ha-device-watch`), installed through HACS as a
   custom repository into `custom_components/device_watch/`, written like home_events.
   - files: manifest, config and options flow, store, sensors, WebSocket command,
     translations;
   - `local_push`, no requirements.
2. **Load:** one Home Assistant restart to load the integration (your call), then add it
   under Devices & services.
3. **Check:** `device_watch/status` against the registry: which devices are watched,
   skipped and offline now. The notification automation stays off until this check
   passes.
4. **Switch over:** apply the migration in section 7, switch the notification automation on,
   and commit.

## 9. Not included

- **More recipients:** notifications to anyone other than Sander (could be a label
  later).
- **Per-device delays:** other than Critical.
- **Reminders:** a device is reported once, and the notification stays until it is
  fixed.
- **Partial failures:** one dead entity on an otherwise working device.
