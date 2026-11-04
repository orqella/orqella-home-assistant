from __future__ import annotations

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_TOKEN, CONF_URL, DEFAULT_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default=DEFAULT_URL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
        vol.Optional(CONF_TOKEN, default=""): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)


class JarvisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Connect to a running Jarvis API."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            url = str(user_input[CONF_URL]).strip().rstrip("/")
            token = str(user_input.get(CONF_TOKEN, "") or "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}

            try:
                session = async_get_clientsession(self.hass)
                async with asyncio.timeout(10):
                    async with session.get(
                        f"{url}/health", headers=headers
                    ) as response:
                        if response.status == 401:
                            errors["base"] = "invalid_auth"
                        elif response.status != 200:
                            errors["base"] = "cannot_connect"
                        else:
                            body = await response.json(content_type=None)
                            if not isinstance(body, dict) or not body.get("ok"):
                                errors["base"] = "cannot_connect"
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - show a useful form error
                _LOGGER.exception("Unexpected error checking Jarvis")
                errors["base"] = "unknown"

            if not errors:
                self._async_abort_entries_match({CONF_URL: url})
                return self.async_create_entry(
                    title="Jarvis",
                    data={CONF_URL: url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                DATA_SCHEMA, user_input
            ),
            errors=errors,
        )
