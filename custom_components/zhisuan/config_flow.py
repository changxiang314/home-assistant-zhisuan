"""Config flow for ZhiSuan (挚算智联) integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)

# ConfigFlowResult 类型别名是 HA 2024.4+ 才有的
# 老版本 (2024.3 及更早) 没有这名字，但有等价的 FlowResult
try:
    from homeassistant.config_entries import ConfigFlowResult  # type: ignore[attr-defined]
except ImportError:
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult  # type: ignore[no-redef]
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ZhisuanApi,
    ZhisuanApiError,
    ZhisuanAuthError,
    ZhisuanConnectionError,
)
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_ENVIRONMENT,
    CONF_HOME_ID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_USERNAME,
    DEFAULT_ENVIRONMENT,
    DEFAULT_NAME,
    DOMAIN,
    ENV_DEV,
    ENV_PROD,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): cv.string,
        vol.Required(CONF_CLIENT_SECRET): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ENVIRONMENT, default=DEFAULT_ENVIRONMENT): vol.In(
            [ENV_DEV, ENV_PROD]
        ),
        vol.Optional(CONF_REGION, default="LOCAL"): cv.string,
    }
)


async def _validate_and_fetch_homes(
    hass, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Try to log in with the supplied credentials; return home list on success."""
    session = async_get_clientsession(hass)
    api = ZhisuanApi(
        client_id=data[CONF_CLIENT_ID],
        client_secret=data[CONF_CLIENT_SECRET],
        session=session,
        environment=data[CONF_ENVIRONMENT],
        region=data.get(CONF_REGION, "LOCAL"),
    )
    await api.async_login(data[CONF_USERNAME], data[CONF_PASSWORD])
    homes = await api.async_get_homes()
    return homes


class ZhisuanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZhiSuan."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, Any] | None = None
        self._homes: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Step 1: 填凭证
    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # 用 (client_id, username) 作为 unique_id，去重
            await self.async_set_unique_id(
                f"{user_input[CONF_CLIENT_ID]}:{user_input[CONF_USERNAME]}"
            )
            self._abort_if_unique_id_configured()

            try:
                self._homes = await _validate_and_fetch_homes(self.hass, user_input)
            except ZhisuanAuthError:
                errors["base"] = "invalid_auth"
            except ZhisuanConnectionError:
                errors["base"] = "cannot_connect"
            except ZhisuanApiError:
                _LOGGER.exception("Unexpected API error during config flow")
                errors["base"] = "unknown"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                # 只有一个 home 就直接进；多个让用户选
                if len(self._homes) == 1:
                    user_input[CONF_HOME_ID] = self._homes[0]["homeId"]
                    return self.async_create_entry(
                        title=DEFAULT_NAME,
                        data=user_input,
                    )
                if len(self._homes) == 0:
                    # 没家庭，先进去再让用户去挚算 APP 建
                    user_input[CONF_HOME_ID] = 0
                    return self.async_create_entry(
                        title=DEFAULT_NAME,
                        data=user_input,
                    )
                self._credentials = user_input
                return await self.async_step_home()

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: 选 home（多 home 时）
    # ------------------------------------------------------------------
    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._credentials is not None

        if user_input is not None:
            self._credentials[CONF_HOME_ID] = user_input[CONF_HOME_ID]
            return self.async_create_entry(
                title=DEFAULT_NAME,
                data=self._credentials,
            )

        home_options = {
            home["homeId"]: f"{home['homeName']} (ID: {home['homeId']})"
            for home in self._homes
        }
        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOME_ID): vol.In(home_options)}
            ),
        )

    # ------------------------------------------------------------------
    # Reauth 流程
    # ------------------------------------------------------------------
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            new_data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate_and_fetch_homes(self.hass, new_data)
            except ZhisuanAuthError:
                errors["base"] = "invalid_auth"
            except (ZhisuanConnectionError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): cv.string}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        return ZhisuanOptionsFlow(config_entry)


class ZhisuanOptionsFlow(OptionsFlow):
    """Handle options (e.g. switch environment, change home)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENVIRONMENT,
                        default=self._entry.data.get(
                            CONF_ENVIRONMENT, DEFAULT_ENVIRONMENT
                        ),
                    ): vol.In([ENV_DEV, ENV_PROD]),
                }
            ),
        )
