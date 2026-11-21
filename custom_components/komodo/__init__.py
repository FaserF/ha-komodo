"""The Komodo integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from komodo_api.lib import ApiKeyInitOptions

from .base import KomodoBase
from .const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_HOST,
    DOMAIN,
    enabled_container_categories,
)
from .sensors.container import container_sensor_category
from .services import async_setup_services, async_unload_services
from .utils import _SUPPORTS_VIA_DEVICE_ID

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.UPDATE, Platform.BUTTON, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Komodo from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    komodo = KomodoBase(
        hass,
        entry.data[CONF_HOST],
        ApiKeyInitOptions(entry.data[CONF_API_KEY], entry.data[CONF_API_SECRET]),
        entry_id=entry.entry_id,
    )
    try:
        await komodo.test_connection()
    except Exception as e:
        _LOGGER.exception("Error setting up Komodo API: {e}")
        await komodo.close()
        raise ConfigEntryNotReady from e

    komodo.container_categories = enabled_container_categories(entry.options)
    hass.data[DOMAIN][entry.entry_id] = komodo
    await komodo.coordinator.async_config_entry_first_refresh()

    # Clean up obsolete or orphaned devices and entities from earlier versions
    _async_cleanup_obsolete_entities(hass, entry.entry_id)

    # Register the main Komodo device and server devices up front, so the stack
    # devices of all platforms can reference them via their device registry id.
    device_reg = dr.async_get(hass)
    base_url = komodo.coordinator.host_url
    main_device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Komodo",
        manufacturer="Komodo",
        model="Fleet Controller",
        sw_version=komodo.coordinator.komodo_version,
        configuration_url=base_url,
    )

    komodo.coordinator.server_device_ids = {
        server.id: device_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, server.id)},
            name=server.name,
            manufacturer="Komodo",
            sw_version=server.periphery_version,
            configuration_url=f"{base_url.rstrip('/')}/servers/{server.id}" if base_url else None,
            **(
                {"via_device_id": main_device.id}
                if _SUPPORTS_VIA_DEVICE_ID
                else {"via_device": (DOMAIN, entry.entry_id)}
            ),
        ).id
        for server in komodo.coordinator.data.servers.values()
        if server.id and not server.is_unknown
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)
    komodo.start_websocket()
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Enable or disable the container sensors of a category when its option changes.

    The listener also fires on reconfigure (data change), so only touch the
    categories whose option really changed - otherwise individually enabled
    container sensors would be disabled again.
    """
    komodo: KomodoBase = hass.data[DOMAIN][entry.entry_id]
    categories = enabled_container_categories(entry.options)
    changed = categories ^ komodo.container_categories
    if not changed:
        return

    entity_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        category = container_sensor_category(entity.unique_id)
        if category not in changed:
            continue
        enabled = category in categories
        if enabled and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            entity_reg.async_update_entity(entity.entity_id, disabled_by=None)
        elif not enabled and entity.disabled_by is None:
            entity_reg.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )

    # Reload so newly created sensors pick up the new enabled default.
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        komodo = hass.data[DOMAIN].pop(entry.entry_id)
        await komodo.close()
        await async_unload_services(hass)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device (stack) from the UI."""
    entity_reg = er.async_get(hass)
    entities = er.async_entries_for_device(
        entity_reg,
        device_entry.id,
        include_disabled_entities=True,
    )
    return not any(entity.config_entry_id == config_entry.entry_id for entity in entities)


@callback
def _async_cleanup_obsolete_entities(hass: HomeAssistant, entry_id: str) -> None:
    """Clean up obsolete or orphaned devices and entities from previous versions."""
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    # Clean up obsolete entities
    for entity in list(er.async_entries_for_config_entry(entity_reg, entry_id)):
        uid = entity.unique_id
        # Obsolete empty server entities, e.g. <entry_id>__server_state
        if f"{entry_id}__" in uid:
            _LOGGER.info("Removing obsolete empty server entity %s (%s)", entity.entity_id, uid)
            entity_reg.async_remove(entity.entity_id)
        # Obsolete old-style procedure button entities <entry_id>_button_<id>
        elif "_button_6a" in uid:
            _LOGGER.info("Removing obsolete procedure button entity %s (%s)", entity.entity_id, uid)
            entity_reg.async_remove(entity.entity_id)

    # Clean up phantom Unknown Server devices
    for device in list(dr.async_entries_for_config_entry(device_reg, entry_id)):
        has_empty_ident = any(
            ident[0] == DOMAIN and (not ident[1] or ident[1] == "") for ident in device.identifiers
        )
        if has_empty_ident or (device.name and device.name.strip().startswith("Unknown Server")):
            _LOGGER.info("Removing obsolete phantom server device %s (%s)", device.name, device.id)
            device_reg.async_remove_device(device.id)
