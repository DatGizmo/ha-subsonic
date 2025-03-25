from __future__ import annotations

import asyncio
import logging
from typing import Any
from datetime import timedelta
from urllib.parse import urlencode

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    BrowseMedia,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    async_process_play_media_url
)

from homeassistant.config_entries import ConfigEntry

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle, dt

# from .subsonic import SubsonicApi
from .const import DOMAIN, LOGGER

CONF_MEDIAPLAYER = "media_players"
DEFAULT_NAME = 'Subsonic'
PLAYLIST_UPDAET_INTERVAL = timedelta(seconds=300)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """ Setup subsonic media player platform. """
    async_add_entities([
        SubsonicEntity(
            hass,
            entry
        )
    ])

class SubsonicEntity(MediaPlayerEntity):
    _attr_media_content_type = MediaType.MUSIC
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        MediaPlayerEntityFeature.BROWSE_MEDIA |
        MediaPlayerEntityFeature.PLAY_MEDIA |
        MediaPlayerEntityFeature.PLAY |
        MediaPlayerEntityFeature.PAUSE |
        MediaPlayerEntityFeature.STOP |
        MediaPlayerEntityFeature.SEEK |
        MediaPlayerEntityFeature.PREVIOUS_TRACK |
        MediaPlayerEntityFeature.NEXT_TRACK |
        MediaPlayerEntityFeature.CLEAR_PLAYLIST |
        MediaPlayerEntityFeature.VOLUME_MUTE |
        MediaPlayerEntityFeature.VOLUME_SET |
        MediaPlayerEntityFeature.VOLUME_STEP |
        MediaPlayerEntityFeature.SELECT_SOURCE |
        MediaPlayerEntityFeature.TURN_OFF |
        MediaPlayerEntityFeature.TURN_ON
    )

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.__api = None
        self._status = None
        self._connect_task = None
        self._currentSong = None
        self._playlist = None
        self._current_playlist: str | None = None
        self._all_playlists = None
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def title(self) -> str:
        return "Subsonic" if self.entry is None else self.entry.title

    @property
    def api(self) -> SubsonicApi:
        if self.__api is None:
            self.__api = self.hass.data[DOMAIN]

        return self.__api

    @property
    def index(self) -> int:
        if self._status:
            return int(self._status.get("currentIndex"))
        return -1

    async def async_update(self) -> None:
        try:
            self._status = await self.api.jukeboxStatus()
            if(self._status):
                self._playlist = await self.api.jukeboxPlaylist()
                self._attr_media_position_updated_at = dt.utcnow()
                self._attr_media_position = int(self._status.get("position"))
                if(self._playlist):
                    self._currentSong = self._playlist[self.index]
                await self._update_playlists()
            else:
                this._currentSong = None
        except TimeoutError as error:
            LOGGER.warning(f"TimeoutError: {error}")

    @property
    def state(self) -> MediaPlayerState:
        """Return the media state."""
        if not self._status or (self._status.get("playing") == 'false' and self.index == -1):
            return MediaPlayerState.IDLE
        if self._status.get("playing") == 'false':
            return MediaPlayerState.PAUSED
        if self._status.get("playing") == 'true':
            return MediaPlayerState.PLAYING

        return MediaPlayerState.OFF

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
        if self._currentSong:
            return self._currentSong.get("duration")
        return "0"

        return None

    @property
    def media_title(self):
        if self._currentSong:
            return self._currentSong.get("title")
        return ""

    @property
    def media_artist(self):
        if self._currentSong:
            return self._currentSong.get("artist")
        return ""

    @property
    def media_album_name(self):
        if self._currentSong:
            return self._currentSong.get("album")
        return ""

    @property
    def media_content_id(self):
        if self._currentSong:
            return self._currentSong.get("path")
        return ""

    @property
    def volume_level(self):
        if self._status:
            return float(self._status.get("gain"))
        return 0.0

    @property
    def source(self):
        return self._current_playlist

    async def async_set_volume_level(self, volume: float) -> None:
        await self.api.jukeboxControl("setGain", gain=volume)

    async def async_volume_up(self) -> None:
        vol = self.volume_level()
        volume = float(vol + 0.03)
        await self.api.jukeboxControl("setGain", gain=volume)

    async def async_volume_down(self) -> None:
        vol = self.volume_level()
        volume = float(vol - 0.03)
        await self.api.jukeboxControl("setGain", gain=volume)

    async def async_media_play(self) -> None:
        """Service to send the MPD the command for play/pause."""
        await self.api.jukeboxControl("start")

    async def async_media_pause(self) -> None:
        """Service to send the MPD the command for play/pause."""
        await self.api.jukeboxControl("stop")

    async def async_media_stop(self) -> None:
        await self.api.jukeboxControl("skip", index=self.index, offset=0)
        await self.api.jukeboxControl("stop")

    async def async_media_next_track(self) -> None:
        curIndex = self.index
        new = curIndex + 1
        if(new >= len(self._playlist)):
            new = len(self._playlist) - 1
        await self.api.jukeboxControl("skip", index=new)

    async def async_media_previous_track(self) -> None:
        curIndex = self.index
        new = curIndex - 1
        if(new < 0):
            new = 0
        await self.api.jukeboxControl("skip", index=new)

    async def async_media_seek(self, position: float) -> None:
        await self.api.jukeboxControl("skip", index=self.index, offset=int(position))

    @Throttle(PLAYLIST_UPDAET_INTERVAL)
    async def _update_playlists(self, **kwargs: Any) -> None:
        self._attr_source_list = []
        self._all_playlists = await self.api.getPlaylists()
        for pl in self._all_playlists:
            self._attr_source_list.append(pl.get("name"))

    async def async_select_source(self, source: str) -> None:
        await self.async_play_media(MediaType.PLAYLIST, source)

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        if media_type == MediaType.PLAYLIST:
            if self._attr_source_list and media_id in self._attr_source_list:
                self._current_playlist = media_id
            else:
                self._current_playlist = None
            for pl in self._all_playlists:
                if(pl.get("name") == media_id):
                    songList = await self.api.getPlaylist(pl.get("id"))
                    songIds = []
                    for s in songList.get("songs"):
                        songIds.append(s.get("id"))
                    await self.api.jukeboxControl("set", id=songIds)
            await self.api.jukeboxControl("start")

    async def async_turn_off(self) -> None:
        """Service to send the MPD the command to stop playing."""
        self._currentSong = None
        await self.api.jukeboxControl('clear')
        if(-1 != self.index):
            await self.api.jukeboxControl('remove', index=0)
            await self.async_update()

    async def async_turn_on(self) -> None:
        """Service to send the MPD the command to start playing."""
        if(-1 < self.index):
            await self.api.jukeboxControl('start', self.index)
        elif(self._current_playlist):
            await self.api.jukeboxControl('start', index=0)
        self._attr_status = MediaPlayerState.IDLE
        await self._update_playlists(no_throttle=True)

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        LOGGER.warning(f"id: {media_content_id}")
        LOGGER.warning(f"type: {media_content_type}")

        # Call async_browse_media with the content filter
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_id.startswith("media-source://subonic"),
        )
