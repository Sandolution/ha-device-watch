"""The watcher: which devices are offline or low on battery, and since when.

Everything is derived from the registries, never from a list. A device that
joins tomorrow is watched tomorrow; a label on the device page is the only way
to change how one is treated. The index of what to look at is rebuilt whenever
the device, entity or label registry changes, and a state change is only looked
at when it belongs to an entity in that index - a dictionary lookup, so the
cost of listening to every state change in the house stays small.

What has been reported is kept in a Store. That is the difference between this
and a template: after a restart every entity's last_changed is the restart, so
anything reading those alone would think every offline device had just gone
and report it again, and would never take back a notification for a device
that came back while Home Assistant was down.

Reporting is on a clock (TICK_SECONDS) rather than a timer per device: one
thing to cancel, and the group window falls out of it - whatever reached its
delay since the first one in the window goes out together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (EVENT_HOMEASSISTANT_STARTED,
                                 EVENT_STATE_CHANGED, STATE_OFF, STATE_ON,
                                 STATE_UNAVAILABLE, STATE_UNKNOWN)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (async_call_later,
                                         async_track_time_interval)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (CONF_BATTERY_CLEAR, CONF_BATTERY_LOW, CONF_BATTERY_MINUTES,
                    CONF_CRITICAL_MINUTES, CONF_GROUP_MINUTES,
                    CONF_OFFLINE_MINUTES, DEFAULTS, EVENT_BATTERY_LOW,
                    EVENT_BATTERY_OK, EVENT_OFFLINE, EVENT_ONLINE, LABEL_CRITICAL,
                    LABEL_NO_BATTERY, LABEL_UNWATCHED, LABELS,
                    REBUILD_DELAY_SECONDS, SIGNAL_UPDATED, STARTUP_GRACE_SECONDS,
                    STORAGE_KEY, STORAGE_VERSION, TICK_SECONDS)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Watched:
    """What the index knows about one device. Rebuilt, never edited."""

    device_id: str
    name: str
    area: str | None
    parent_id: str | None
    # Entities from the device's own integration. Powercalc, utility_meter and
    # the like hang theirs on other integrations' devices and keep a state
    # when the device itself is gone, so they would keep it "online" forever.
    own: list[str]
    # Battery entities from any integration: a battery level is a battery
    # level whoever measures it.
    battery: list[str]
    # Only filled on a parent device - see _is_offline.
    connectivity: list[str]
    skip: str | None
    critical: bool
    battery_watched: bool

    @property
    def watched(self) -> bool:
        return self.skip is None


def _all_devices(registry: dr.DeviceRegistry):
    """Every device entry, however this version of the registry hands them out.

    Iterating the registry's devices yields entries from 2026.10 on; before
    that it yielded ids. Looking an id up through the mapping is deprecated,
    async_get is not.
    """
    for item in registry.devices:
        entry = item if isinstance(item, dr.DeviceEntry) else registry.async_get(item)
        if entry is not None:
            yield entry


def _config_entry(device: dr.DeviceEntry) -> str | None:
    """The device's own integration entry (renamed from primary_config_entry)."""
    if hasattr(device, "config_entry_id"):
        return device.config_entry_id
    return device.primary_config_entry


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _parse(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


class DeviceWatcher:
    """Keeps the index, the records, and the clock."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.options: dict[str, Any] = {**DEFAULTS, **entry.options}
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # devices: device_id -> record (see _record). groups: group_id -> the
        # device ids of that notification still reported offline.
        self._data: dict[str, Any] = {"devices": {}, "groups": {}}
        self._index: dict[str, Watched] = {}
        self._entity_device: dict[str, str] = {}
        self._parents: set[str] = set()
        self._labels: dict[str, str | None] = {}
        # Devices that reached their delay and wait for the group window.
        self._pending: list[str] = []
        self._pending_since: datetime | None = None
        # When each parent last came back, so its children get their own delay
        # counted from there instead of being reported the moment it returns.
        self._parent_offline: dict[str, bool] = {}
        self._parent_back: dict[str, datetime] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._rebuild_unsub: CALLBACK_TYPE | None = None
        self._started_unsub: CALLBACK_TYPE | None = None
        self.ready = False

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._data = {"devices": dict(data.get("devices") or {}),
                          "groups": dict(data.get("groups") or {})}
        self._ensure_labels(create=True)
        self._rebuild_index()

        bus = self.hass.bus
        self._unsubs += [
            bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._schedule_rebuild),
            bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._schedule_rebuild),
            bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, self._on_labels),
        ]
        if self.hass.is_running:
            # A reload or an options change: the entities have their real
            # states already.
            self._begin()
        else:
            self._started_unsub = bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_started)

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._started_unsub:
            self._started_unsub()
            self._started_unsub = None
        if self._rebuild_unsub:
            self._rebuild_unsub()
            self._rebuild_unsub = None
        await self._store.async_save(self._data)

    @callback
    def _on_started(self, _event: Event) -> None:
        # A one-time listener is gone once it has fired; removing it again
        # on unload would log an error.
        self._started_unsub = None
        # MQTT entities start out unavailable and only come back as their
        # availability messages arrive; judging them before that would report
        # half the house.
        self._unsubs.append(async_call_later(
            self.hass, STARTUP_GRACE_SECONDS, self._begin))

    @callback
    def _begin(self, _now: datetime | None = None) -> None:
        self.ready = True
        self._unsubs += [
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state,
                                       event_filter=self._is_ours),
            async_track_time_interval(self.hass, self._tick,
                                      timedelta(seconds=TICK_SECONDS)),
        ]
        self._evaluate_all()
        self._tick()

    @callback
    def _ensure_labels(self, create: bool = False) -> None:
        """Find the three labels by name, creating them on startup only.

        Created on startup and not when somebody deletes one: deleting a label
        is a decision, and a label that comes straight back would be an
        argument with the user.
        """
        registry = lr.async_get(self.hass)
        for name, (icon, description) in LABELS.items():
            label = registry.async_get_label_by_name(name)
            if label is None and create:
                label = registry.async_create(name, icon=icon,
                                              description=description)
            self._labels[name] = label.label_id if label else None

    @callback
    def _on_labels(self, _event: Event) -> None:
        self._ensure_labels()
        self._schedule_rebuild(_event)

    @callback
    def _schedule_rebuild(self, _event: Event) -> None:
        if self._rebuild_unsub is None:
            self._rebuild_unsub = async_call_later(
                self.hass, REBUILD_DELAY_SECONDS, self._rebuild)

    @callback
    def _rebuild(self, _now: datetime | None = None) -> None:
        self._rebuild_unsub = None
        self._rebuild_index()
        if self.ready:
            self._evaluate_all()

    # ------------------------------------------------------------------ index

    @callback
    def _rebuild_index(self) -> None:
        devices = dr.async_get(self.hass)
        entities = er.async_get(self.hass)
        areas = ar.async_get(self.hass)
        all_devices = list(_all_devices(devices))
        parents = {d.via_device_id for d in all_devices if d.via_device_id}
        unwatched = self._labels.get(LABEL_UNWATCHED)
        critical = self._labels.get(LABEL_CRITICAL)
        no_battery = self._labels.get(LABEL_NO_BATTERY)

        index: dict[str, Watched] = {}
        entity_device: dict[str, str] = {}
        for device in all_devices:
            entries = er.async_entries_for_device(entities, device.id)
            own = [e for e in entries
                   if e.config_entry_id == _config_entry(device)]
            battery = [e.entity_id for e in entries
                       if e.domain in ("sensor", "binary_sensor")
                       and (e.device_class or e.original_device_class) == "battery"]
            # A connectivity sensor reading "off" means "offline" on a bridge
            # (Zigbee2MQTT's only entity), but elsewhere it is often off by
            # design - a phone's Android Auto connection. Parents only.
            connectivity = [e.entity_id for e in own
                            if device.id in parents and e.domain == "binary_sensor"
                            and (e.device_class or e.original_device_class) == "connectivity"]
            if device.entry_type is dr.DeviceEntryType.SERVICE:
                skip = "service device"
            elif device.disabled_by:
                skip = "disabled"
            elif not own:
                skip = "no own entities"
            elif unwatched and unwatched in device.labels:
                skip = "labelled Unwatched"
            else:
                skip = None
            area = areas.async_get_area(device.area_id) if device.area_id else None
            watched = Watched(
                device_id=device.id,
                name=device.name_by_user or device.name or device.id,
                area=area.name if area else None,
                parent_id=device.via_device_id,
                own=[e.entity_id for e in own],
                battery=battery,
                connectivity=connectivity,
                skip=skip,
                critical=bool(critical and critical in device.labels),
                battery_watched=not (no_battery and no_battery in device.labels),
            )
            index[device.id] = watched
            # Parents are followed even when unwatched: their children's delay
            # counts from the moment they come back.
            if watched.watched or device.id in parents:
                for entity_id in watched.own:
                    entity_device[entity_id] = device.id
            if watched.watched:
                for entity_id in battery:
                    entity_device.setdefault(entity_id, device.id)
        self._index = index
        self._entity_device = entity_device
        self._parents = parents

    # ------------------------------------------------------------- evaluation

    @callback
    def _is_ours(self, event_data: Any) -> bool:
        return event_data["entity_id"] in self._entity_device

    @callback
    def _on_state(self, event: Event) -> None:
        device_id = self._entity_device.get(event.data["entity_id"])
        if device_id and self._evaluate(device_id):
            self._changed()

    @callback
    def _evaluate_all(self) -> None:
        changed = False
        # Records of devices that are gone or no longer watched are let go
        # first, taking any notification back with them.
        for device_id in list(self._data["devices"]):
            watched = self._index.get(device_id)
            if watched is None or not watched.watched:
                changed |= self._release_offline(device_id)
                changed |= self._release_battery(device_id)
                self._data["devices"].pop(device_id, None)
            elif not watched.battery_watched:
                changed |= self._release_battery(device_id)
        for device_id in self._index:
            changed |= self._evaluate(device_id)
        if changed:
            self._changed()

    def _is_offline(self, watched: Watched) -> bool | None:
        """True when every own entity is unavailable; None when none has a state."""
        states = [s for s in (self.hass.states.get(e) for e in watched.own) if s]
        if not states:
            return None
        return all(s.state == STATE_UNAVAILABLE
                   or (s.entity_id in watched.connectivity and s.state == STATE_OFF)
                   for s in states)

    def _offline_since(self, watched: Watched) -> datetime:
        """When the last own entity went: the moment the device was gone."""
        states = [s for s in (self.hass.states.get(e) for e in watched.own) if s]
        return max(s.last_changed for s in states)

    def _record(self, device_id: str) -> dict[str, Any]:
        return self._data["devices"].setdefault(device_id, {
            "name": None,
            "offline_since": None,
            "offline_reported": False,
            "group_id": None,
            "battery_entity": None,
            "battery_level": None,
            "battery_low_since": None,
            "battery_reported": False,
        })

    @callback
    def _evaluate(self, device_id: str) -> bool:
        """Bring one device's record up to date. True when anything changed."""
        watched = self._index.get(device_id)
        if watched is None:
            return False
        offline = self._is_offline(watched)
        self._follow_parent(device_id, offline)
        if not watched.watched or offline is None:
            return False

        record = self._record(device_id)
        changed = record["name"] != watched.name
        record["name"] = watched.name
        if offline:
            if record["offline_since"] is None:
                record["offline_since"] = _iso(self._offline_since(watched))
                changed = True
        elif record["offline_since"] is not None:
            changed |= self._release_offline(device_id)
            record["offline_since"] = None
            if device_id in self._pending:
                self._pending.remove(device_id)
            changed = True

        if watched.battery_watched and not offline:
            changed |= self._evaluate_battery(device_id, watched, record)
        return changed

    @callback
    def _follow_parent(self, device_id: str, offline: bool | None) -> None:
        if offline is None or device_id not in self._parents:
            return
        if self._parent_offline.get(device_id) and not offline:
            self._parent_back[device_id] = dt_util.utcnow()
        self._parent_offline[device_id] = offline

    def _battery(self, watched: Watched) -> tuple[str | None, float | None, bool, bool]:
        """The device's battery: (entity, level, low, cleared).

        The first battery entity with a usable state wins. Low and cleared are
        both False between the two thresholds, which is what keeps a reading
        wobbling around 20 % from going back and forth.
        """
        low_at = self.options[CONF_BATTERY_LOW]
        clear_at = self.options[CONF_BATTERY_CLEAR]
        for entity_id in watched.battery:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            if entity_id.startswith("binary_sensor."):
                return entity_id, None, state.state == STATE_ON, state.state == STATE_OFF
            try:
                level = float(state.state)
            except ValueError:
                continue
            return entity_id, level, level < low_at, level > clear_at
        return None, None, False, False

    @callback
    def _evaluate_battery(self, device_id: str, watched: Watched,
                          record: dict[str, Any]) -> bool:
        entity_id, level, low, cleared = self._battery(watched)
        if entity_id is None:
            return False
        changed = (record["battery_entity"], record["battery_level"]) != (entity_id, level)
        record["battery_entity"] = entity_id
        record["battery_level"] = level
        if low:
            if record["battery_low_since"] is None:
                record["battery_low_since"] = _iso(dt_util.utcnow())
                changed = True
        elif cleared:
            changed |= self._release_battery(device_id)
            if record["battery_low_since"] is not None:
                record["battery_low_since"] = None
                changed = True
        elif not record["battery_reported"] and record["battery_low_since"] is not None:
            # Back between the thresholds before an hour was up: it was not
            # low for long enough, so the hour starts again next time.
            record["battery_low_since"] = None
            changed = True
        return changed

    # ------------------------------------------------------------- reporting

    def _parent_is_offline(self, watched: Watched) -> bool:
        parent = self._index.get(watched.parent_id) if watched.parent_id else None
        return bool(parent and self._is_offline(parent))

    @callback
    def _tick(self, _now: datetime | None = None) -> None:
        now = dt_util.utcnow()
        changed = False
        normal = timedelta(minutes=self.options[CONF_OFFLINE_MINUTES])
        fast = timedelta(minutes=self.options[CONF_CRITICAL_MINUTES])
        battery_after = timedelta(minutes=self.options[CONF_BATTERY_MINUTES])

        for device_id, record in self._data["devices"].items():
            watched = self._index.get(device_id)
            if watched is None or not watched.watched:
                continue
            since = _parse(record["offline_since"])
            if since and not record["offline_reported"] \
                    and device_id not in self._pending \
                    and not self._parent_is_offline(watched):
                start = max(since, self._parent_back.get(watched.parent_id, since))
                if now - start >= (fast if watched.critical else normal):
                    self._pending.append(device_id)
                    self._pending_since = self._pending_since or now
            low_since = _parse(record["battery_low_since"])
            if low_since and not record["battery_reported"] \
                    and watched.battery_watched and now - low_since >= battery_after:
                record["battery_reported"] = True
                self.hass.bus.async_fire(EVENT_BATTERY_LOW,
                                         {"device": self._battery_payload(device_id)})
                changed = True

        window = timedelta(minutes=self.options[CONF_GROUP_MINUTES])
        if self._pending and self._pending_since and now - self._pending_since >= window:
            changed |= self._send_group()
        if changed:
            self._changed()

    @callback
    def _send_group(self) -> bool:
        devices = [d for d in self._pending
                   if self._data["devices"].get(d, {}).get("offline_since")
                   and not self._data["devices"][d]["offline_reported"]]
        self._pending = []
        self._pending_since = None
        if not devices:
            return False
        group_id = uuid.uuid4().hex[:12]
        for device_id in devices:
            self._data["devices"][device_id]["offline_reported"] = True
            self._data["devices"][device_id]["group_id"] = group_id
        self._data["groups"][group_id] = devices
        payloads = [self._offline_payload(d) for d in devices]
        self.hass.bus.async_fire(EVENT_OFFLINE, {
            "group_id": group_id,
            "devices": payloads,
            "count": len(payloads),
            "critical": any(p["critical"] for p in payloads),
        })
        return True

    @callback
    def _release_offline(self, device_id: str) -> bool:
        """A reported device is back, or no longer watched: say so once."""
        record = self._data["devices"].get(device_id)
        if not record or not record["offline_reported"]:
            return False
        group_id = record["group_id"]
        payload = self._offline_payload(device_id)
        record["offline_reported"] = False
        record["group_id"] = None
        group = [d for d in self._data["groups"].get(group_id, []) if d != device_id]
        if group:
            self._data["groups"][group_id] = group
        else:
            self._data["groups"].pop(group_id, None)
        self.hass.bus.async_fire(EVENT_ONLINE, {
            "group_id": group_id,
            "device": payload,
            "remaining": [self._offline_payload(d) for d in group],
        })
        return True

    @callback
    def _release_battery(self, device_id: str) -> bool:
        record = self._data["devices"].get(device_id)
        if not record or not record["battery_reported"]:
            return False
        payload = self._battery_payload(device_id)
        record["battery_reported"] = False
        record["battery_low_since"] = None
        self.hass.bus.async_fire(EVENT_BATTERY_OK, {"device": payload})
        return True

    # --------------------------------------------------------------- output

    def _base_payload(self, device_id: str) -> dict[str, Any]:
        watched = self._index.get(device_id)
        record = self._data["devices"].get(device_id, {})
        return {
            "device_id": device_id,
            "name": watched.name if watched else record.get("name") or device_id,
            "area": watched.area if watched else None,
        }

    def _offline_payload(self, device_id: str) -> dict[str, Any]:
        record = self._data["devices"].get(device_id, {})
        watched = self._index.get(device_id)
        return {
            **self._base_payload(device_id),
            "since": record.get("offline_since"),
            "critical": bool(watched and watched.critical),
            "battery_level": record.get("battery_level"),
        }

    def _battery_payload(self, device_id: str) -> dict[str, Any]:
        record = self._data["devices"].get(device_id, {})
        return {
            **self._base_payload(device_id),
            "since": record.get("battery_low_since"),
            "entity_id": record.get("battery_entity"),
            "level": record.get("battery_level"),
        }

    def offline_devices(self) -> list[dict[str, Any]]:
        return [self._offline_payload(d) for d, r in self._data["devices"].items()
                if r["offline_reported"]]

    def battery_devices(self) -> list[dict[str, Any]]:
        return [self._battery_payload(d) for d, r in self._data["devices"].items()
                if r["battery_reported"]]

    def status(self) -> list[dict[str, Any]]:
        """Every device considered, for checking the watcher against reality."""
        out = []
        for device_id, watched in sorted(self._index.items(),
                                         key=lambda item: item[1].name.lower()):
            record = self._data["devices"].get(device_id, {})
            parent = self._index.get(watched.parent_id) if watched.parent_id else None
            out.append({
                "device_id": device_id,
                "name": watched.name,
                "area": watched.area,
                "watched": watched.watched,
                "skip": watched.skip,
                "critical": watched.critical,
                "battery_watched": watched.battery_watched,
                "parent": parent.name if parent else None,
                "own_entities": len(watched.own),
                "offline": self._is_offline(watched),
                "offline_since": record.get("offline_since"),
                "offline_reported": record.get("offline_reported", False),
                "pending": device_id in self._pending,
                "battery_entity": record.get("battery_entity"),
                "battery_level": record.get("battery_level"),
                "battery_low_since": record.get("battery_low_since"),
                "battery_reported": record.get("battery_reported", False),
            })
        return out

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(lambda: self._data, 5)
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
