"""Diagnostics support for Komodo."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_API_SECRET, CONF_HOST, DOMAIN

TO_REDACT = {
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_HOST,
    "api_key",
    "api_secret",
    "client_secret",
    "token",
    "password",
    "secret",
    "host",
    "url",
    "ip_address",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return strictly redacted diagnostics for a Komodo config entry."""
    komodo = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    coordinator = getattr(komodo, "coordinator", None)
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success if coordinator else None,
            "komodo_version": getattr(coordinator, "komodo_version", None),
        },
    }
