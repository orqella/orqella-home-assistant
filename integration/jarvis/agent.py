from __future__ import annotations

import asyncio
import logging
from typing import Literal

import aiohttp

from homeassistant.components.conversation import ConversationResult
from homeassistant.components.conversation.models import (
    AbstractConversationAgent,
    ConversationInput,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_TOKEN, CONF_URL, DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class JarvisConversationAgent(AbstractConversationAgent):
    """Send Assist text to the local Jarvis router."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.url = str(entry.data[CONF_URL]).rstrip("/")
        self.token = str(entry.data.get(CONF_TOKEN, "") or "")

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["en"]

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        payload = {
            "text": user_input.text,
            "conversation_id": user_input.conversation_id,
            "language": user_input.language,
            "device_id": user_input.device_id,
            "satellite_id": user_input.satellite_id,
        }
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(DEFAULT_TIMEOUT):
                async with session.post(
                    f"{self.url}/api/conversation",
                    json=payload,
                    headers=headers,
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"Jarvis returned HTTP {resp.status}: {body!r}"
                        )
            if not isinstance(body, dict):
                raise RuntimeError("Jarvis returned a non-object response")
        except Exception as exc:  # noqa: BLE001 - fail with a spoken message
            _LOGGER.warning("Jarvis request failed: %s", exc)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech("I couldn't reach Jarvis.")
            return ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
                continue_conversation=False,
            )

        speech = str(body.get("say") or "").strip() or "Done."
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return ConversationResult(
            response=response,
            conversation_id=body.get("conversation_id") or user_input.conversation_id,
            continue_conversation=bool(body.get("continue_conversation", False)),
        )
