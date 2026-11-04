from __future__ import annotations

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .agent import JarvisConversationAgent
from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["stt"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register Jarvis as a conversation agent and local STT engine."""
    agent = JarvisConversationAgent(hass, entry)
    conversation.async_set_agent(hass, entry, agent)
    entry.async_on_unload(lambda: conversation.async_unset_agent(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unregister the conversation agent and STT entity."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        conversation.async_unset_agent(hass, entry)
    return unloaded
