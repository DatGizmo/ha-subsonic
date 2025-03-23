from .const import DOMAIN, LOGGER
from .subsonicApi import SubsonicApi

from homeassistant.const import (
    __version__,
    Platform
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession

PLATFORMS = [Platform.MEDIA_PLAYER]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    result = False
    session = async_get_clientsession(hass)
    userAgent = f"Home Assistant/{__version__}"

    LOGGER.info(f"Subsonic Setup")

    navidrome = SubsonicApi(session=session,
                            userAgent=userAgent,
                            config=entry.data)

    try:
        result = await navidrome.ping()
    except Exception as e:
        raise ConfigEntryNotReady("Could not connect to Subsonic API") from err

    hass.data[DOMAIN] = navidrome
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return result


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    del hass.data[DOMAIN]
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
