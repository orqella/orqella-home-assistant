from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import override

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_TOKEN, CONF_URL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Jarvis Whisper STT entity."""
    async_add_entities([JarvisSTTEntity(hass, entry)])


class JarvisSTTEntity(stt.SpeechToTextEntity):
    """Send Assist audio to Jarvis's local Whisper model."""

    _attr_name = "Jarvis Whisper"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.url = str(entry.data[CONF_URL]).rstrip("/")
        self.token = str(entry.data.get(CONF_TOKEN, "") or "")
        self._attr_unique_id = f"{entry.entry_id}-stt"

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    @property
    def audio_processing(self) -> stt.SpeechAudioProcessing:
        # Jarvis runs faster-whisper with its own VAD.
        return stt.SpeechAudioProcessing(
            requires_external_vad=False,
            prefers_auto_gain_enabled=True,
            prefers_noise_reduction_enabled=True,
        )

    @override
    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        audio = b"".join([chunk async for chunk in stream])
        if not audio:
            return stt.SpeechResult("", stt.SpeechResultState.SUCCESS)

        headers = {
            "Content-Type": "application/octet-stream",
            "X-Sample-Rate": str(int(getattr(metadata.sample_rate, "value", metadata.sample_rate))),
            "X-Channels": str(int(getattr(metadata.channel, "value", metadata.channel))),
            "X-Bit-Rate": str(int(getattr(metadata.bit_rate, "value", metadata.bit_rate))),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(180):
                async with session.post(
                    f"{self.url}/api/stt",
                    data=audio,
                    headers=headers,
                ) as response:
                    body = await response.json(content_type=None)
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}: {body!r}")
            if not isinstance(body, dict):
                raise RuntimeError("Jarvis returned a non-object response")
        except Exception as exc:  # noqa: BLE001 - HA expects an error result
            _LOGGER.warning("Jarvis STT failed: %s", exc)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        return stt.SpeechResult(
            str(body.get("text") or ""),
            stt.SpeechResultState.SUCCESS,
        )
