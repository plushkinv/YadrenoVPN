"""
Service for working with the API 3X-UI panel.

Provides:
- Authorization through sessions
- Client management (creation, deletion, updating)
- Obtaining traffic statistics
- Managing inbound connections
"""

import aiohttp
import asyncio
import logging
import json
import re
import uuid
import time
import urllib.parse
from typing import Optional, Dict, Any, List
from config import RETRY_CONFIG

logger = logging.getLogger(__name__)

DEFAULT_PANEL_TIMEOUT_SECONDS = 15
API_PROFILE_LEGACY = "legacy_inbounds"
API_PROFILE_CLIENTS = "clients_api"
BOT_API_TOKEN_NAME = "YadrenoVPN Bot"
JSON_INBOUND_FIELDS = ("settings", "streamSettings", "sniffing")
SETTING_BASE_LEGACY = "/panel/setting"
SETTING_BASE_API = "/panel/api/setting"
MTPROTO_MULTI_CLIENT_MIN_VERSION = (3, 5, 0)
READ_ONLY_POST_ENDPOINTS = {
    "/login",
    "/panel/api/inbounds/onlines",
    "/panel/api/clients/onlines",
    "/panel/api/clients/lastOnline",
    "/panel/api/clients/onlinesByGuid",
    "/panel/api/clients/clientIpsByGuid",
    "/panel/api/clients/activeInbounds",
}


from .base import (
    BaseVPNClient,
    PanelClientState,
    PanelDatabaseBackup,
    PanelServerSnapshot,
    VPNAPIError,
    build_legacy_panel_snapshot,
)
from bot.services.panel_sync_coordinator import panel_sync_coordinator
from bot.utils.inbounds import (
    filter_regular_inbounds,
    filter_visible_inbounds,
    is_ignored_inbound,
    is_mtproto_inbound,
)


class StaleAPIProfileError(Exception):
    """The panel has changed its API profile; the operation must be selected again."""


class TransientPanelError(VPNAPIError):
    """Temporary network unavailability of the panel."""


class XUIClient(BaseVPNClient):
    """
    Client for working with API 3X-UI panel.
    
    Uses session authentication (cookie-based).
    IMPORTANT: For 3X-UI, cookies can be tied to an IP, so we use unsafe=True for CookieJar.
    """
    
    def __init__(self, server: dict):
        """
        Initializing the client.

        Args:
            server: Dictionary with server data from the database
        """
        self.server = server
        self.server_id = server.get('id')
        self.host = server['host']
        self.port = server['port']
        self.protocol = server.get('protocol', 'https')
        # We guarantee that the path starts with a slash, but does NOT end with it
        # strip('/') removes slashes from both the beginning and the end
        path = server.get('web_base_path', '').strip('/')
        # Now add one slash to the beginning (if the path is not empty)
        path = f"/{path}" if path else ""

        self.base_url = f"{self.protocol}://{self.host}:{self.port}{path}"

        self.session: Optional[aiohttp.ClientSession] = None
        self.is_authenticated = False

        # Support for different generations of 3x-ui.
        # auth_mode/panel_mode:
        #   legacy = v2.x cookie; csrf = v3.0+ cookie + X-CSRF-Token;
        #   bearer = v3.0+ via Authorization: Bearer for /panel/api/*.
        # api_profile:
        #   legacy_inbounds = legacy client operations via /panel/api/inbounds/*
        #   clients_api = first-class clients API from 3x-ui v3.1.0+.
        self.panel_mode: Optional[str] = None
        self.auth_mode: Optional[str] = None
        self.cookie_authenticated = False
        self.csrf_token: Optional[str] = None
        self.api_token: Optional[str] = server.get('api_token') or None
        self.panel_version: Optional[str] = server.get('panel_version') or None
        self.api_profile: Optional[str] = server.get('panel_api_profile') or None
        self._profile_verified = False
        self.api_token_diagnostic: Optional[str] = None

        # Panel settings cache (subPort/subPath/subDomain/...) from setting/all.
        # Use build_subscription_url() - requested once per session.
        self._panel_settings: Optional[Dict[str, Any]] = None

        logger.debug(
            f"Инициализирован XUIClient для {server['name']}: {self.base_url} "
            f"(api_token={'есть' if self.api_token else 'нет'})"
        )

    def _has_cookie_credentials(self) -> bool:
        """Whether a complete username/password pair is available for session login."""
        return bool(
            str(self.server.get("login") or "").strip()
            and str(self.server.get("password") or "").strip()
        )

    def _uses_api_token_only(self) -> bool:
        """Whether this server intentionally has only a Bearer token."""
        return bool(self.api_token) and not self._has_cookie_credentials()
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Creates a session if there is none."""
        if self.session is None or self.session.closed:
            # Unsafe=True is important for IP addresses and self-signed certificates
            connector = aiohttp.TCPConnector(ssl=False)
            jar = aiohttp.CookieJar(unsafe=True)
            try:
                timeout_seconds = float(RETRY_CONFIG.get("timeout_seconds", DEFAULT_PANEL_TIMEOUT_SECONDS))
            except (TypeError, ValueError):
                timeout_seconds = DEFAULT_PANEL_TIMEOUT_SECONDS
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            self.session = aiohttp.ClientSession(connector=connector, cookie_jar=jar, timeout=timeout)
            self.is_authenticated = False
            self.cookie_authenticated = False
            logger.debug(f"Создана новая сессия для {self.server['name']}")
        return self.session
    
    async def _reset_session(self) -> None:
        """
        Resets the current session.

        Called when connection errors occur to recreate the session.
        The CSRF token is cleared - it is tied to the server session.
        panel_mode and api_token are NOT reset - this is policy, not session
        condition. They are reset separately by _invalidate_api_token() during rotation
        token in the panel.
        """
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug(f"Ошибка при закрытии сессии: {e}")
        self.session = None
        self.is_authenticated = False
        self.cookie_authenticated = False
        self.csrf_token = None
        logger.debug(f"Сессия сброшена для {self.server['name']}")

    async def _invalidate_api_token(self) -> None:
        """
        Resets the Bearer token (when rotating in the panel or 404 on a Bearer request).

        Clears the token in the database (via update_server_api_token) so that the next time
        When launched, the bot did not try to use an invalid token.
        """
        if self.api_token is None:
            return
        self.api_token = None
        # panel_mode will be recreated at the next login() - it may turn out to be 'csrf'
        # (if the token is expired, but the panel is still v3.0+) or 'bearer' again (if
        # background login will have time to pull out a new token).
        self.panel_mode = None
        self.auth_mode = None
        if self.server_id is not None:
            try:
                from database.db_servers import update_server_api_token
                update_server_api_token(self.server_id, None)
            except Exception as e:
                logger.warning(f"Не удалось очистить api_token в БД для server_id={self.server_id}: {e}")

    @staticmethod
    def _load_json_field(value: Any, default: Optional[Any] = None) -> Any:
        """Returns a dict/list from a JSON string or an already unpacked value."""
        if default is None:
            default = {}
        if value in (None, ""):
            return default.copy() if isinstance(default, (dict, list)) else default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default.copy() if isinstance(default, (dict, list)) else default
        if isinstance(value, (dict, list)):
            return value
        return default.copy() if isinstance(default, (dict, list)) else default

    @staticmethod
    def _json_field_to_text(value: Any, empty: str = "{}") -> str:
        """Normalizes the inbound JSON field to a string for old bot logic."""
        if value in (None, ""):
            return empty
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return empty

    @classmethod
    def _normalize_inbound(cls, inbound: Dict[str, Any]) -> Dict[str, Any]:
        """Converts inbound v3.1.0 with nested JSON to legacy form with strings."""
        if not isinstance(inbound, dict):
            return inbound
        normalized = dict(inbound)
        for field in JSON_INBOUND_FIELDS:
            normalized[field] = cls._json_field_to_text(normalized.get(field), "{}")
        return normalized

    @staticmethod
    def _normalize_tg_id(value: Any) -> int:
        """3x-ui v3.1.0 stores tgId as int64; empty and garbage values = 0."""
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _client_identifier_from_entry(client: Dict[str, Any]) -> str:
        """Returns the technical client ID for old update/delete."""
        if not isinstance(client, dict):
            return ""
        return (
            client.get("id")
            or client.get("password")
            or client.get("auth")
            or client.get("email")
            or ""
        )

    @classmethod
    def _find_client_in_inbounds(
        cls,
        inbounds: List[Dict[str, Any]],
        inbound_id: Optional[int] = None,
        client_uuid: Optional[str] = None,
        email: Optional[str] = None,
    ) -> tuple:
        """Searches for a client in the already loaded inbound list without an additional request to the panel."""
        for inbound in inbounds or []:
            if inbound_id is not None and inbound.get("id") != inbound_id:
                continue
            settings = cls._load_json_field(inbound.get("settings", "{}"))
            for client in settings.get("clients", []):
                if email and client.get("email") == email:
                    return inbound, client
                if client_uuid and cls._client_identifier_from_entry(client) == client_uuid:
                    return inbound, client
        return None, None

    @classmethod
    def _build_add_client_result(
        cls,
        client: Dict[str, Any],
        inbound_id: int,
        email: str,
        fallback_uuid: str,
        expire_time: int,
        total_gb: int,
        fallback_sub_id: str,
    ) -> Dict[str, Any]:
        """Collects a single add_client result from the actual panel entry."""
        client_uuid = (
            client.get("uuid")
            or cls._client_identifier_from_entry(client)
            or fallback_uuid
        )
        return {
            "uuid": client_uuid,
            "email": email,
            "inbound_id": inbound_id,
            "expire_time": client.get("expiryTime", expire_time),
            "total_gb": total_gb,
            "sub_id": client.get("subId") or fallback_sub_id,
        }

    def _save_api_token(self, token: str) -> None:
        """Saves the Bearer token in the object and database."""
        self.api_token = token
        self.server["api_token"] = token
        if self.server_id is not None:
            try:
                from database.db_servers import update_server_api_token
                update_server_api_token(self.server_id, token)
            except Exception as e:
                logger.warning(f"Не удалось сохранить api_token в БД: {e}")

    def _save_panel_info(self) -> None:
        """Saves specific version/profile panels in an object and database."""
        self.server["panel_version"] = self.panel_version
        self.server["panel_api_profile"] = self.api_profile
        if self.server_id is None:
            return
        try:
            from database.db_servers import update_server_panel_info
            update_server_panel_info(self.server_id, self.panel_version, self.api_profile)
        except Exception as e:
            logger.debug(f"Не удалось сохранить диагностику панели в БД: {e}")

    def _build_client_payload_from_record(
        self,
        record: Dict[str, Any],
        fallback_email: Optional[str] = None,
        fallback_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Converts ClientRecord/get_inbounds client to model.Client payload v3.1.0.

        In the /clients/get response, the id field is the numeric ID of the database record, and the client's UUID
        lies in uuid. In payload update, the id field must be exactly UUID.
        """
        if not isinstance(record, dict):
            record = {}

        uuid_value = record.get("uuid")
        record_id = record.get("id")
        if not uuid_value and isinstance(record_id, str):
            uuid_value = record_id
        is_mtproto_record = "secret" in record or "adTag" in record
        if not uuid_value and fallback_uuid and not is_mtproto_record:
            uuid_value = fallback_uuid

        payload: Dict[str, Any] = {
            "email": record.get("email") or fallback_email or "",
            "security": record.get("security", "auto"),
            "limitIp": record.get("limitIp", 1),
            "totalGB": record.get("totalGB", 0),
            "expiryTime": record.get("expiryTime", 0),
            "enable": record.get("enable", True),
            "tgId": self._normalize_tg_id(record.get("tgId", 0)),
            "subId": record.get("subId", ""),
            "comment": record.get("comment", ""),
            "reset": record.get("reset", 0),
        }

        if uuid_value:
            payload["id"] = uuid_value
        for field in ("password", "auth", "flow", "secret", "adTag"):
            value = record.get(field)
            if value:
                payload[field] = value
        reverse = record.get("reverse")
        if reverse:
            payload["reverse"] = reverse
        return {k: v for k, v in payload.items() if v != ""}

    @staticmethod
    def _split_clients_api_record(record: Dict[str, Any]) -> tuple:
        """Returns (client, inboundIds) from response /panel/api/clients/get/:email."""
        if not isinstance(record, dict):
            return {}, []
        if isinstance(record.get("client"), dict):
            client = dict(record["client"])
            inbound_ids = record.get("inboundIds") or client.get("inboundIds") or []
        else:
            client = dict(record)
            inbound_ids = record.get("inboundIds") or []
        if not isinstance(inbound_ids, list):
            inbound_ids = []
        return client, [int(i) for i in inbound_ids if str(i).isdigit()]

    @staticmethod
    def _version_tuple(version: Optional[str]) -> tuple:
        """Returns a tuple of the 3x-ui version for safe comparison."""
        if not version:
            return ()
        text = str(version).strip().lstrip("vV")
        parts = []
        for part in text.split("."):
            match = re.match(r"(\d+)", part)
            if not match:
                break
            parts.append(int(match.group(1)))
        return tuple(parts)

    @classmethod
    def _version_at_least(cls, version: Optional[str], minimum: tuple) -> bool:
        parts = cls._version_tuple(version)
        if not parts:
            return False
        padded = parts + (0,) * (len(minimum) - len(parts))
        return padded[:len(minimum)] >= minimum

    def _supports_mtproto_multi_client(self, profile: Optional[str] = None) -> bool:
        """Whether this panel can manage one MTProto secret per client."""
        effective_profile = profile or self.api_profile
        return (
            effective_profile == API_PROFILE_CLIENTS
            and self._version_at_least(
                self.panel_version,
                MTPROTO_MULTI_CLIENT_MIN_VERSION,
            )
        )

    def _setting_bases(self) -> List[str]:
        """
        Returns the namespace order for setting routes.

        Before 3x-ui v3.3.0, settings lived in /panel/setting/*, starting from v3.3.0
        they moved to /panel/api/setting/*. If the version is unknown, first
        We are trying the old way so as not to change the behavior of existing panels.
        """
        if self._uses_api_token_only():
            return [SETTING_BASE_API]
        if self._version_at_least(self.panel_version, (3, 3, 0)):
            return [SETTING_BASE_API, SETTING_BASE_LEGACY]
        return [SETTING_BASE_LEGACY, SETTING_BASE_API]

    def _setting_endpoints(self, suffix: str) -> List[str]:
        suffix = suffix if suffix.startswith("/") else f"/{suffix}"
        return [f"{base}{suffix}" for base in self._setting_bases()]

    async def _raw_json_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple:
        """Raw request without login/_request so that probes don’t get stuck in loops."""
        session = await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        try:
            async with session.request(method, url, json=data, headers=headers or {}) as resp:
                text = await resp.text()
                try:
                    body = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    body = {}
                return resp.status, body
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Raw API запрос {method} {endpoint} упал: {e}")
            return 0, {}

    async def _fetch_panel_version(self) -> Optional[str]:
        """Determines the panel version via server/status with fallback to updateInfo."""
        headers = self._build_headers("GET")

        status, data = await self._raw_json_request(
            "GET",
            "/panel/api/server/status",
            headers=headers,
        )
        if status == 200 and isinstance(data, dict):
            obj = data.get("obj")
            if isinstance(obj, dict):
                for key in ("panelVersion", "version", "currentVersion"):
                    value = obj.get(key)
                    if isinstance(value, str) and value:
                        return value

        status, data = await self._raw_json_request(
            "GET",
            "/panel/api/server/getPanelUpdateInfo",
            headers=headers,
        )
        if status == 200 and isinstance(data, dict):
            obj = data.get("obj")
            if isinstance(obj, dict):
                for key in ("currentVersion", "panelVersion", "version"):
                    value = obj.get(key)
                    if isinstance(value, str) and value:
                        return value
        return None

    async def _detect_api_profile(self) -> str:
        """Feature-probe: v3.1.0+ has /panel/api/clients/list/paged."""
        headers = self._build_headers("GET")
        status, data = await self._raw_json_request(
            "GET",
            "/panel/api/clients/list/paged",
            headers=headers,
        )
        if status == 200 and isinstance(data, dict) and data.get("success"):
            return API_PROFILE_CLIENTS
        if status in (404, 405):
            return API_PROFILE_LEGACY
        if status == 0 or status >= 500:
            raise TransientPanelError(
                "Пакетный Clients API временно недоступен; профиль панели не изменён"
            )
        raise VPNAPIError(
            f"Не удалось определить профиль Clients API (HTTP {status})"
        )

    async def _refresh_panel_metadata(self, force: bool = False) -> None:
        """Updates the version/profile of the panel and writes the cache to servers."""
        if not force and self.api_profile in (API_PROFILE_LEGACY, API_PROFILE_CLIENTS):
            if self.panel_version:
                return

        version = await self._fetch_panel_version()
        profile = await self._detect_api_profile()

        if version:
            self.panel_version = version
        self.api_profile = profile
        self._profile_verified = True
        self._save_panel_info()

    async def _ensure_api_profile(self) -> str:
        """Ensures that the API profile for customer transactions is selected."""
        if not self.is_authenticated:
            await self.login()
        if self.api_profile in (API_PROFILE_LEGACY, API_PROFILE_CLIENTS) and self._profile_verified:
            return self.api_profile
        if self.api_profile not in (API_PROFILE_LEGACY, API_PROFILE_CLIENTS) or not self._profile_verified:
            await self._refresh_panel_metadata(force=True)
        return self.api_profile or API_PROFILE_LEGACY

    @staticmethod
    def _is_legacy_client_endpoint(endpoint: str) -> bool:
        """True for old client endpoints that disappeared in 3x-ui v3.1.0+."""
        if endpoint == "/panel/api/inbounds/addClient":
            return True
        if endpoint == "/panel/api/inbounds/onlines":
            return True
        if endpoint.startswith("/panel/api/inbounds/updateClient/"):
            return True
        if endpoint.startswith("/panel/api/inbounds/") and "/delClient/" in endpoint:
            return True
        if endpoint.startswith("/panel/api/inbounds/") and "/resetClientTraffic/" in endpoint:
            return True
        return False

    async def _raise_if_stale_legacy_profile(self, endpoint: str) -> None:
        """
        With 404 on the old client endpoint, it rechecks the API profile.

        If the panel is already v3.1.0+ and supports clients_api, the current request cannot
        retrace with the same URL: the calling operation must reselect the endpoint.
        """
        if self.api_profile != API_PROFILE_LEGACY:
            return
        if not self._is_legacy_client_endpoint(endpoint):
            return

        old_version = self.panel_version or "unknown"
        logger.info(
            f"Legacy client endpoint вернул 404 на {self.server['name']}; "
            f"перепроверяем профиль API панели"
        )
        await self._refresh_panel_metadata(force=True)
        if self.api_profile == API_PROFILE_CLIENTS:
            logger.info(
                f"Панель {self.server['name']} переключилась "
                f"{old_version}/{API_PROFILE_LEGACY} → "
                f"{self.panel_version or 'unknown'}/{API_PROFILE_CLIENTS}; "
                f"повторяем операцию через clients API"
            )
            raise StaleAPIProfileError("Профиль API панели изменился на clients_api")

    async def _run_with_stale_profile_retry(self, operation):
        """Repeats the operation once if 404 indicates an API profile upgrade."""
        try:
            return await operation()
        except StaleAPIProfileError:
            return await operation()

    async def _get_clients_api_record(self, email: str, log_error: bool = False) -> Optional[Dict[str, Any]]:
        """Returns the v3.1.0 client record by email or None."""
        encoded_email = urllib.parse.quote(email, safe="")
        try:
            result = await self._request(
                "GET",
                f"/panel/api/clients/get/{encoded_email}",
                retry=False,
                log_error=log_error,
            )
        except VPNAPIError:
            return None
        obj = result.get("obj")
        return obj if isinstance(obj, dict) else None

    async def _get_verified_mtproto_client(
        self,
        email: str,
        inbound_id: int,
        attempts: int = 3,
    ) -> Dict[str, Any]:
        """Re-read a 3X-UI 3.5+ MTProto client and verify its generated secret."""
        delays = RETRY_CONFIG.get("delays", [])
        for attempt in range(max(1, attempts)):
            record = await self._get_clients_api_record(email, log_error=False)
            if record:
                client, inbound_ids = self._split_clients_api_record(record)
                if inbound_id in inbound_ids and client.get("secret"):
                    return client
            if attempt < max(1, attempts) - 1:
                delay = delays[min(attempt, len(delays) - 1)] if delays else 0
                if delay:
                    await asyncio.sleep(delay)
        raise VPNAPIError(
            f"3X-UI не подтвердил индивидуальный MTProto-secret для клиента {email} "
            f"в inbound {inbound_id}. Требуется 3X-UI 3.5.0 или новее."
        )

    async def _recover_added_client(
        self,
        profile: str,
        inbound_id: int,
        email: str,
        fallback_uuid: str,
        expire_time: int,
        total_gb: int,
        fallback_sub_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Checks whether the panel managed to create a client before the network failure."""
        if profile == API_PROFILE_CLIENTS:
            record = await self._get_clients_api_record(email, log_error=False)
            if not record:
                return None
            client, inbound_ids = self._split_clients_api_record(record)
            if inbound_id not in inbound_ids:
                return None
            return self._build_add_client_result(
                client,
                inbound_id,
                email,
                fallback_uuid,
                expire_time,
                total_gb,
                fallback_sub_id,
            )

        try:
            _, panel_client = await self._find_panel_client(
                inbound_id=inbound_id,
                email=email,
            )
        except Exception as e:
            logger.debug(f"Не удалось проверить созданного клиента {email} после сбоя add_client: {e}")
            return None
        if not panel_client:
            return None
        return self._build_add_client_result(
            panel_client,
            inbound_id,
            email,
            fallback_uuid,
            expire_time,
            total_gb,
            fallback_sub_id,
        )

    async def _find_panel_client(
        self,
        inbound_id: Optional[int] = None,
        client_uuid: Optional[str] = None,
        email: Optional[str] = None,
        include_ignored: bool = False,
    ) -> tuple:
        """Looks up the client in /inbounds/list and returns (inbound, client)."""
        if self._supports_mtproto_multi_client():
            inbounds = await self._get_all_inbounds()
            if not include_ignored:
                inbounds = filter_visible_inbounds(inbounds)
        else:
            inbounds = await self.get_inbounds(include_ignored=include_ignored)
        for inbound in inbounds:
            if inbound_id is not None and inbound.get("id") != inbound_id:
                continue
            settings = self._load_json_field(inbound.get("settings", "{}"))
            for client in settings.get("clients", []):
                if email and client.get("email") == email:
                    return inbound, client
                if client_uuid and self._client_identifier_from_entry(client) == client_uuid:
                    return inbound, client
        return None, None
    
    async def _detect_panel_version(self) -> tuple:
        """
        Determines the panel version via probe GET /csrf-token.

        - HTTP 200 + JSON.obj → v3.0+ (CSRF middleware is active).
        - HTTP 404 → v2.x (endpoint does not exist).
        - Any other error → consider legacy (safe fallback).

        The request goes directly through session, without _request, so as not to get stuck in a loop.

        Returns:
            Tuple (mode, csrf_token): ('csrf', '<token>') or ('legacy', None).
        """
        session = await self._ensure_session()
        url = f"{self.base_url}/csrf-token"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()
                        token = data.get('obj') if isinstance(data, dict) else None
                        if isinstance(token, str) and token:
                            logger.info(f"Обнаружена 3x-ui v3.0+ на {self.server['name']} (CSRF активен)")
                            return ('csrf', token)
                    except (json.JSONDecodeError, aiohttp.ContentTypeError):
                        pass
                # 404 or other - consider v2.x
                logger.debug(f"Probe /csrf-token вернул {resp.status}, считаем v2.x legacy режим")
                return ('legacy', None)
        except asyncio.TimeoutError as e:
            raise TransientPanelError("Таймаут при проверке версии панели") from e
        except aiohttp.ClientError as e:
            raise TransientPanelError(f"Ошибка подключения при проверке версии панели: {e}") from e

    async def _fetch_api_token(self) -> Optional[str]:
        """
        Pulls the Bearer token from the v3.0+ panel.

        On v3.0.2+/v3.1.0 uses /panel/setting/apiTokens.
        On v3.3.0+ uses /panel/api/setting/apiTokens:
        - takes an enabled token with the name YadrenoVPN Bot;
        - if there is no token, creates it;
        - if the token is found disabled, does not turn it back on and remains CSRF.

        On v3.0.0 it falls back to the old /panel/setting/getApiToken.

        Returns:
            Token or None if it was not possible to obtain.
        """
        if self.csrf_token is None:
            logger.debug("Невозможно вытянуть api_token: csrf_token не установлен")
            return None

        headers = self._build_headers("GET", force_cookie=True, include_csrf_for_get=True)

        # The new token API appeared after v3.0.0. In v3.3.0 it moved
        # from /panel/setting/* to /panel/api/setting/*.
        last_status = None
        last_endpoint = None
        for endpoint in self._setting_endpoints("apiTokens"):
            last_endpoint = endpoint
            status, data = await self._raw_json_request(
                "GET",
                endpoint,
                headers=headers,
            )
            last_status = status
            if status != 200 or not isinstance(data, dict) or not data.get("success"):
                logger.debug(f"GET {endpoint} вернул HTTP {status}, пробуем следующий setting namespace")
                continue

            rows = data.get("obj") or []
            if isinstance(rows, dict):
                rows = rows.get("items") or rows.get("rows") or rows.get("tokens") or []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict) or row.get("name") != BOT_API_TOKEN_NAME:
                        continue
                    enabled = row.get("enabled", row.get("isEnabled", True))
                    if enabled is False or enabled == 0:
                        self.api_token_diagnostic = (
                            f"API-токен '{BOT_API_TOKEN_NAME}' найден, но отключён в панели. "
                            "Бот остаётся в режиме cookie+CSRF."
                        )
                        logger.warning(self.api_token_diagnostic)
                        return None
                    token = row.get("token") or row.get("apiToken")
                    if isinstance(token, str) and token:
                        self._save_api_token(token)
                        return token

            create_headers = self._build_headers("POST", force_cookie=True, include_csrf_for_get=True)
            create_endpoint = endpoint + "/create"
            status, data = await self._raw_json_request(
                "POST",
                create_endpoint,
                data={"name": BOT_API_TOKEN_NAME},
                headers=create_headers,
            )
            if status == 200 and isinstance(data, dict) and data.get("success"):
                obj = data.get("obj")
                if isinstance(obj, dict):
                    token = obj.get("token") or obj.get("apiToken")
                    if isinstance(token, str) and token:
                        self._save_api_token(token)
                        return token
                token = data.get("obj")
                if isinstance(token, str) and token:
                    self._save_api_token(token)
                    return token
            logger.debug(f"Не удалось создать api_token через {create_endpoint}: HTTP {status}, data={data}")
            return None

        if last_status not in (0, 404, 405):
            logger.debug(f"GET {last_endpoint} вернул HTTP {last_status}, fallback getApiToken")

        # Old endpoint v3.0.0.
        headers = self._build_headers("GET", force_cookie=True, include_csrf_for_get=True)
        for endpoint in self._setting_endpoints("getApiToken"):
            status, data = await self._raw_json_request(
                "GET",
                endpoint,
                headers=headers,
            )
            if status != 200:
                logger.debug(f"GET {endpoint} вернул {status}")
                continue
            if not isinstance(data, dict) or not data.get('success'):
                continue
            token = data.get('obj')
            if isinstance(token, str) and token:
                self._save_api_token(token)
                return token
        return None

    async def _try_bearer_validate(self) -> bool:
        """
        An easy probe request to check the relevance of the Bearer token.

        Does GET /panel/api/server/status with Authorization: Bearer.
        - 200 → token is valid, go to 'bearer' mode.
        - 404/401 → the token is invalid (rotated in the panel).
        - 0 → the panel is temporarily unavailable, the token is not cleared.
        - Other → considered invalid.

        Returns:
            True if the token is working.
        """
        if not self.api_token:
            return False
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Authorization": f"Bearer {self.api_token}",
        }
        status, _ = await self._raw_json_request(
            "GET",
            "/panel/api/server/status",
            headers=headers,
        )
        if status == 200:
            return True
        if status == 0:
            raise TransientPanelError("Панель недоступна при проверке Bearer-токена")
        logger.info(f"Bearer-токен невалиден (HTTP {status}), нужно обновить")
        return False

    def _build_headers(
        self,
        method: str,
        force_cookie: bool = False,
        include_csrf_for_get: bool = False,
    ) -> Dict[str, str]:
        """
        Collects HTTP headers depending on panel_mode.

        - legacy: only basic AJAX headers.
        - csrf: adds X-CSRF-Token for unsafe methods.
        - bearer: adds Authorization: Bearer (CSRF is not needed).
        - force_cookie: for the old /panel/setting/* Bearer is not suitable, you need cookie+CSRF.
          The new /panel/api/setting/* works like a regular namespace API.
        """
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if not force_cookie and self.panel_mode == 'bearer' and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        elif self.csrf_token and (
            include_csrf_for_get or method.upper() not in ('GET', 'HEAD', 'OPTIONS')
        ):
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        retry: bool = True,
        log_error: bool = True,
    ) -> Dict[str, Any]:
        """Coordinate mutating API calls and delegate the HTTP request."""
        normalized_method = method.upper()
        read_only_post = (
            normalized_method == "POST"
            and (
                endpoint in READ_ONLY_POST_ENDPOINTS
                or endpoint in {
                    f"{SETTING_BASE_LEGACY}/all",
                    f"{SETTING_BASE_API}/all",
                }
            )
        )
        if normalized_method not in {"GET", "HEAD", "OPTIONS"} and not read_only_post:
            async with panel_sync_coordinator.regular():
                return await self._request_impl(
                    method,
                    endpoint,
                    data=data,
                    retry=retry,
                    log_error=log_error,
                )
        return await self._request_impl(
            method,
            endpoint,
            data=data,
            retry=retry,
            log_error=log_error,
        )

    async def _request_impl(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        retry: bool = True,
        log_error: bool = True
    ) -> Dict[str, Any]:
        """
        Makes an HTTP request to the API.
        
        Args:
            method: HTTP method (GET, POST)
            endpoint: Relative path (starts with /panel/... or /login)
            data: Data for POST request
            retry: Whether to repeat on errors
            
        Returns:
            API response as a dictionary
            
        Raises:
            VPNAPIError: When the request failed
        """
        # URL = https://ip:port/secret_path/panel/...
        url = f"{self.base_url}{endpoint}"

        attempts = RETRY_CONFIG["max_attempts"] if retry else 1
        delays = RETRY_CONFIG["delays"]
        is_cookie_setting_route = endpoint.startswith(f"{SETTING_BASE_LEGACY}/")

        for attempt in range(attempts):
            try:
                # We get the current session (important, since it can be recreated in _reset_session)
                session = await self._ensure_session()

                # If authorization is required and we are not authorized (and this is not a login request)
                if not self.is_authenticated and endpoint != "/login":
                    await self.login()

                if is_cookie_setting_route and endpoint != "/login":
                    await self._ensure_cookie_auth()

                # Headers are collected AFTER login() - panel_mode is defined there
                # and csrf_token/api_token needed for _build_headers are installed.
                headers = self._build_headers(
                    method,
                    force_cookie=is_cookie_setting_route,
                    include_csrf_for_get=is_cookie_setting_route,
                )

                logger.debug(f"API запрос: {method} {url} (mode={self.panel_mode})")

                async with session.request(method, url, json=data, headers=headers) as response:
                    text = await response.text()

                    # Bearer is rotten (rotated in the panel) - reset the token, re-login
                    if response.status == 401 and self.panel_mode == 'bearer' and not is_cookie_setting_route:
                        if self._uses_api_token_only():
                            await self._reset_session()
                            raise VPNAPIError(
                                "API-ключ отклонён панелью. Проверьте, что токен включён "
                                "и не был удалён или пересоздан."
                            )
                        logger.warning(
                            f"HTTP 401 в режиме bearer — токен невалиден, "
                            f"переключаемся на обычный логин"
                        )
                        await self._invalidate_api_token()
                        await self._reset_session()
                        if attempt < attempts - 1:
                            continue

                    # CSRF token is out of date (panel restart, etc.) - re-upgrade and repeat
                    if response.status == 403 and (self.panel_mode == 'csrf' or is_cookie_setting_route):
                        logger.info("HTTP 403 — переподтягиваем CSRF-токен")
                        mode, token = await self._detect_panel_version()
                        if mode == 'csrf':
                            self.csrf_token = token
                            if attempt < attempts - 1:
                                continue

                    # Status processing
                    if response.status == 200:
                        try:
                            result = json.loads(text)
                            if result.get("success"):
                                return result
                            
                            # Sometimes success=False but there is msg
                            if "msg" in result and not result["success"]:
                                msg = result["msg"].lower()
                                # Checking for signs of session expiration
                                if any(x in msg for x in ["login", "auth", "session", "token"]):
                                    logger.warning(f"Сессия возможно истекла (msg='{result['msg']}'), пересоздаём...")
                                    await self._reset_session()
                                    if attempt < attempts - 1:
                                        # The session will be recreated on the next request
                                        continue
                                        
                                raise VPNAPIError(result["msg"])
                            return result
                        except json.JSONDecodeError:
                            # Sometimes it returns HTML when redirecting to login
                            if "login" in text.lower():
                                logger.warning("Сессия истекла (редирект на логин), пересоздаём...")
                                await self._reset_session()
                                if attempt < attempts - 1:
                                    # The session will be recreated on the next request
                                    continue
                            logger.error(f"Невалидный JSON: {text[:100]}")
                            raise VPNAPIError("Некорректный ответ сервера")
                    elif response.status == 404:
                         await self._raise_if_stale_legacy_profile(endpoint)
                         # Some versions of X-UI return 404 if the session has expired
                         # We are trying to recreate the session
                         logger.warning(f"HTTP 404 (Endpoint not found) для {url}, сессия возможно истекла. Попытка {attempt+1}/{attempts}")
                         await self._reset_session()
                         if attempt < attempts - 1:
                             continue
                         
                         if log_error:
                             logger.error(f"Endpoint not found после {attempts} попыток: {url}")
                         raise VPNAPIError("Ошибка API: Метод не найден (404). Проверьте настройки сервера.")
                    elif response.status == 401:
                        logger.warning("HTTP 401, пересоздаём сессию...")
                        await self._reset_session()
                        if attempt < attempts - 1:
                            continue
                    
                    raise VPNAPIError(f"HTTP {response.status}: {text[:100]}")
                    
            except TransientPanelError as e:
                logger.warning(f"Панель временно недоступна (попытка {attempt+1}/{attempts}): {e}")
                await self._reset_session()
                if attempt < attempts - 1:
                    await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
                else:
                    raise VPNAPIError(f"Панель недоступна: {e}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                reason = "таймаут подключения" if isinstance(e, asyncio.TimeoutError) else str(e)
                logger.warning(f"Ошибка подключения (попытка {attempt+1}/{attempts}): {reason}")
                # We reset the session in case of connection errors in order to recreate it
                await self._reset_session()
                if attempt < attempts - 1:
                    await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
                else:
                    raise VPNAPIError(f"Панель недоступна: {reason}")
            except StaleAPIProfileError:
                raise
            except VPNAPIError:
                raise
            except Exception as e:
                logger.error(f"Неожиданная ошибка ({type(e).__name__}): {e}", exc_info=True)
                raise VPNAPIError(f"Неожиданная ошибка ({type(e).__name__}): {e}")
        
        raise VPNAPIError("Превышено количество попыток")

    async def _login_with_cookie(self, fetch_token: bool = True) -> bool:
        """
        Regular login via cookie. For v3.0+ adds CSRF.

        fetch_token=True is used by the main login() so that after the cookie login
        receive Bearer. For the old /panel/setting/* fetch_token=False, so as not to call
        recursion when servicing setting routes.
        """
        if not self._has_cookie_credentials():
            raise VPNAPIError(
                "Для этого сервера не сохранены логин и пароль. "
                "Обновите API-ключ или подключите сервер по логину и паролю."
            )

        mode, csrf_token = await self._detect_panel_version()
        self.panel_mode = mode
        self.auth_mode = mode
        self.csrf_token = csrf_token

        session = await self._ensure_session()
        url = f"{self.base_url}/login"
        login_headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if mode == 'csrf' and csrf_token:
            login_headers["X-CSRF-Token"] = csrf_token

        try:
            async with session.post(
                url,
                json={
                    "username": self.server.get("login", ""),
                    "password": self.server.get("password", ""),
                },
                headers=login_headers,
            ) as resp:
                text = await resp.text()
                if resp.status == 200:
                    data = json.loads(text)
                    if data.get("success"):
                        self.is_authenticated = True
                        self.cookie_authenticated = True
                        logger.info(f"✅ Успешная авторизация на {self.server['name']} (режим={mode})")
                    else:
                        raise VPNAPIError(f"Ошибка логина: {data.get('msg')}")
                elif resp.status == 404:
                    raise VPNAPIError(f"Панель недоступна по пути {self.server['web_base_path']}")
                elif resp.status == 403:
                    raise VPNAPIError("Ошибка CSRF при логине (HTTP 403). Возможно, панель v3.0+ требует X-CSRF-Token")
                else:
                    raise VPNAPIError(f"HTTP {resp.status} при логине")
        except aiohttp.ClientConnectorError as e:
            raise TransientPanelError(
                f"Не удалось подключиться к {self.server.get('protocol', 'https')}://"
                f"{self.server['host']}:{self.server['port']}"
            ) from e
        except asyncio.TimeoutError as e:
            raise TransientPanelError("Таймаут при логине") from e
        except aiohttp.ClientError as e:
            raise TransientPanelError(f"Ошибка подключения при логине: {e}") from e
        except json.JSONDecodeError:
            raise VPNAPIError("Некорректный ответ при логине")

        if mode == 'csrf' and fetch_token:
            token = await self._fetch_api_token()
            if token:
                self.panel_mode = 'bearer'
                self.auth_mode = 'bearer'
                logger.info(
                    f"🔑 Вытянут api_token с {self.server['name']}, "
                    f"переключаемся на Bearer-режим (v3.0+)"
                )
            else:
                logger.info(
                    f"Не удалось вытянуть api_token с {self.server['name']}, "
                    f"остаёмся в режиме csrf (cookie + X-CSRF-Token)"
                )

        return True

    async def _ensure_cookie_auth(self) -> bool:
        """Guarantees a cookie session for the old /panel/setting/* even in Bearer mode."""
        if not self._has_cookie_credentials():
            raise VPNAPIError(
                "Эта операция требует cookie-сессию панели. Подключение только по "
                "API-ключу полностью поддерживается в 3X-UI 3.3.0 и новее."
            )
        if self.cookie_authenticated and self.session is not None and not self.session.closed:
            return True
        bearer_token = self.api_token
        was_bearer = self.panel_mode == 'bearer' and bool(bearer_token)
        await self._login_with_cookie(fetch_token=False)
        if was_bearer and bearer_token:
            self.api_token = bearer_token
            self.panel_mode = 'bearer'
            self.auth_mode = 'bearer'
            self.is_authenticated = True
        return True

    async def login(self) -> bool:
        """
        Authorization in the 3X-UI panel with auto-detection of the auth/API profile.

        Algorithm:
        1. If there is a saved api_token, try Bearer validation (without login).
           If successful, set panel_mode='bearer' and check version/profile.
        2. Probe GET /csrf-token → 200 means v3.0+, 404 means v2.x.
        3. On v3.0+: log in with X-CSRF-Token, then pull/create api_token.
        4. On v2.x: regular POST /login without CSRF.

        Returns:
            True if authorization is successful

        Raises:
            VPNAPIError: Authorization error
        """
        logger.info(f"Авторизация на {self.server['name']}...")

        if self.api_token:
            if await self._try_bearer_validate():
                self.panel_mode = 'bearer'
                self.auth_mode = 'bearer'
                self.is_authenticated = True
                self.cookie_authenticated = False
                await self._refresh_panel_metadata(force=True)
                if self._uses_api_token_only() and not self._version_at_least(
                    self.panel_version,
                    (3, 3, 0),
                ):
                    detected = self.panel_version or "не определена"
                    raise VPNAPIError(
                        "Подключение только по API-ключу требует 3X-UI 3.3.0 или новее. "
                        f"Версия панели: {detected}."
                    )
                logger.info(f"✅ Авторизация через Bearer-токен (v3.0+) на {self.server['name']}")
                return True
            if self._uses_api_token_only():
                raise VPNAPIError(
                    "API-ключ недействителен или отключён в панели. "
                    "Создайте либо включите токен в настройках безопасности 3X-UI."
                )
            await self._invalidate_api_token()

        if not self._has_cookie_credentials():
            raise VPNAPIError(
                "Не указан рабочий API-ключ и отсутствуют логин с паролем панели."
            )

        await self._login_with_cookie(fetch_token=True)
        await self._refresh_panel_metadata(force=True)
        return True

    async def _get_all_inbounds(self) -> List[Dict[str, Any]]:
        """Fetch and normalize every inbound returned by the panel."""
        result = await self._request("GET", "/panel/api/inbounds/list")
        obj = result.get("obj", [])
        if not isinstance(obj, list):
            return []
        return [
            self._normalize_inbound(inbound)
            for inbound in obj
            if isinstance(inbound, dict)
        ]

    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        """Return regular single-key inbounds; MTProto is subscription-only."""
        inbounds = filter_regular_inbounds(await self._get_all_inbounds())
        if include_ignored:
            return inbounds
        return filter_visible_inbounds(inbounds)

    async def get_subscription_inbounds(
        self,
        include_ignored: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return subscription inbounds, including MTProto on 3X-UI 3.5+."""
        profile = await self._ensure_api_profile()
        inbounds = await self._get_all_inbounds()
        if not self._supports_mtproto_multi_client(profile):
            skipped = [inbound for inbound in inbounds if is_mtproto_inbound(inbound)]
            if skipped:
                logger.debug(
                    "MTProto inbounds skipped on panel %s: multi-client requires "
                    "3X-UI 3.5.0+ with clients_api (version=%s, profile=%s, ids=%s)",
                    self.server.get("name", self.server_id),
                    self.panel_version or "unknown",
                    profile or "unknown",
                    [inbound.get("id") for inbound in skipped],
                )
            inbounds = filter_regular_inbounds(inbounds)
        if include_ignored:
            return inbounds
        return filter_visible_inbounds(inbounds)

    async def get_sync_snapshot(
        self,
        subscription_mode: bool = False,
    ) -> PanelServerSnapshot:
        """Download all eligible inbounds and all logical clients once."""
        return await self._get_sync_snapshot_impl(subscription_mode)

    async def _get_sync_snapshot_impl(
        self,
        subscription_mode: bool,
    ) -> PanelServerSnapshot:
        profile = await self._ensure_api_profile()
        all_inbounds = await self._get_all_inbounds()
        if subscription_mode and self._supports_mtproto_multi_client(profile):
            inbounds = all_inbounds
        else:
            inbounds = filter_regular_inbounds(all_inbounds)

        snapshot = build_legacy_panel_snapshot(inbounds, api_profile=profile)
        if profile != API_PROFILE_CLIENTS:
            return snapshot

        rows: List[Dict[str, Any]] = []
        page = 1
        page_size = 200
        expected_total: Optional[int] = None
        try:
            while True:
                query = urllib.parse.urlencode({"page": page, "pageSize": page_size})
                result = await self._request(
                    "GET",
                    f"/panel/api/clients/list/paged?{query}",
                )
                obj = result.get("obj")
                if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
                    raise VPNAPIError("Clients API returned an invalid paged client list")
                items = [item for item in obj["items"] if isinstance(item, dict)]
                rows.extend(items)

                if expected_total is None:
                    expected_total = self._traffic_int(
                        obj.get("filtered", obj.get("total"))
                    )
                if expected_total is not None and len(rows) >= expected_total:
                    break
                if not items:
                    break
                if expected_total is None and len(items) < page_size:
                    break
                page += 1
                if page > 10000:
                    raise VPNAPIError("Clients API pagination did not terminate")
        except VPNAPIError as exc:
            # A cached clients profile may survive a panel downgrade. Re-probe
            # only after the paged endpoint itself is confirmed missing. Reuse
            # the inbounds already downloaded for this pass when it is legacy.
            if "404" not in str(exc):
                raise
            if self.api_profile == API_PROFILE_CLIENTS:
                await self._refresh_panel_metadata(force=True)
            if self.api_profile != API_PROFILE_CLIENTS:
                snapshot.api_profile = self.api_profile or API_PROFILE_LEGACY
                return snapshot
            raise

        eligible_ids = {
            int(inbound["id"])
            for inbound in inbounds
            if str(inbound.get("id", "")).isdigit()
        }
        for row in rows:
            email = str(row.get("email") or "").strip()
            normalized_email = email.lower()
            if not normalized_email:
                continue

            previous = snapshot.clients.get(normalized_email)
            raw_ids = row.get("inboundIds") or []
            inbound_ids = {
                int(value)
                for value in raw_ids
                if str(value).isdigit() and int(value) in eligible_ids
            }
            placements = dict(previous.placements) if previous else {}
            identity = {}
            if previous and previous.client:
                identity.update(previous.client)
            identity.update(row)

            traffic_payload = row.get("traffic")
            # In ListPaged a missing optional traffic object means the client
            # has no counter row yet, i.e. a known zero rather than an unknown
            # value requiring a per-email lookup.
            traffic_known = True
            traffic_used = 0
            if isinstance(traffic_payload, dict):
                normalized_traffic = self._traffic_used_from_payload(traffic_payload)
                traffic_used = normalized_traffic if normalized_traffic is not None else 0
            else:
                normalized_traffic = self._traffic_used_from_payload(row)
                if normalized_traffic is not None:
                    traffic_used = normalized_traffic

            snapshot.clients[normalized_email] = PanelClientState(
                email=email,
                client=identity,
                inbound_ids=inbound_ids,
                placements=placements,
                traffic_used=traffic_used,
                traffic_known=traffic_known,
                total_gb=self._first_traffic_int(
                    row,
                    ("totalGB", "total", "trafficLimit"),
                    0,
                ),
                expiry_time=self._first_traffic_int(
                    row,
                    ("expiryTime", "expiry_time", "expire"),
                    0,
                ),
                enable=bool(row.get("enable", True)),
                sub_id=str(row.get("subId") or ""),
                limit_ip=self._first_traffic_int(row, ("limitIp",), 1),
                reset=self._first_traffic_int(row, ("reset",), 0),
                source="clients_api_paged",
            )

        return snapshot
    
    async def get_server_status(self) -> Dict[str, Any]:
        """
        Gets the server status (CPU, memory, uptime).
        
        Returns:
            Dictionary with server status
        """
        try:
            result = await self._request("GET", "/panel/api/server/status")
            return result.get("obj", {})
        except VPNAPIError:
            # Some versions of 3X-UI do not have this endpoint
            return {}

    async def get_stats(self) -> Dict[str, Any]:
        """
        Gets server statistics.
        
        Returns:
            Dictionary with statistics:
            - total_clients: Total number of clients
            - active_clients: Number of active clients (enable=True)
            - total_traffic_bytes: Total traffic (up + down)
            - cpu_percent: CPU load (if available)
            - online: True if the server is available
        """
        try:
            # Aggregate server traffic includes service inbounds such as MTProto,
            # while the manual --! marker still hides an inbound from bot stats.
            inbounds = filter_visible_inbounds(await self._get_all_inbounds())
            
            total_clients = 0
            active_clients = 0
            total_traffic = 0
            
            for inbound in inbounds:
                # Parsing client settings
                settings = self._load_json_field(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                total_clients += len(clients)

                for client in clients:
                    if client.get("enable", True):
                        active_clients += 1
                
                # Traffic inbound
                total_traffic += inbound.get("up", 0)
                total_traffic += inbound.get("down", 0)
            
            # Trying to get the server status (CPU)
            cpu_percent = None
            try:
                status = await self.get_server_status()
                if status:
                    raw_cpu = status.get("cpu")
                    if raw_cpu is not None:
                        try:
                            cpu_percent = int(float(raw_cpu))
                        except (ValueError, TypeError):
                            pass
            except VPNAPIError:
                pass
            
            return {
                "total_clients": total_clients,
                "active_clients": active_clients,
                "online_clients": await self.get_online_clients_count(),
                "total_traffic_bytes": total_traffic,
                "cpu_percent": cpu_percent,
                "online": True
            }
            
        except VPNAPIError as e:
            logger.warning(f"Ошибка получения статистики: {e}")
            return {
                "total_clients": 0,
                "active_clients": 0,
                "online_clients": 0,
                "total_traffic_bytes": 0,
                "cpu_percent": None,
                "online": False,
                "error": str(e)
            }

    async def get_online_clients_count(self) -> int:
        return await self._run_with_stale_profile_retry(
            self._get_online_clients_count_impl
        )

    async def get_nodes(self) -> List[Dict[str, Any]]:
        """
        Gets a list of nodes connected to the 3X-UI master panel.

        Old panels and panels without Node API return 404. For monitoring this
        not a mistake: such a panel simply does not have an accessible list of nodes.
        """
        try:
            result = await self._request(
                "GET",
                "/panel/api/nodes/list",
                retry=False,
                log_error=False,
            )
        except VPNAPIError as e:
            logger.debug(
                f"get_nodes: Node API недоступен на панели "
                f"{self.server.get('name', self.server_id)}: {e}"
            )
            return []

        obj = result.get("obj", [])
        if isinstance(obj, list):
            return [node for node in obj if isinstance(node, dict)]
        return []

    async def _get_online_clients_count_impl(self) -> int:
        """
        Gets the number of users online.
        
        Returns:
            Number of users online
        """
        try:
            profile = await self._ensure_api_profile()
            endpoint = (
                "/panel/api/clients/onlines"
                if profile == API_PROFILE_CLIENTS
                else "/panel/api/inbounds/onlines"
            )
            response = await self._request("POST", endpoint, retry=False, log_error=False)
            if response.get("success") and response.get("obj"):
                return len(response["obj"])
        except StaleAPIProfileError:
            raise
        except VPNAPIError:
            pass
        except Exception as e:
            logger.debug(f"Ошибка получения online пользователей: {e}")
        return 0

    async def add_client(
        self,
        inbound_id: int,
        email: str,
        total_gb: int = 0,
        expire_days: int = 30,
        limit_ip: int = 1,
        enable: bool = True,
        tg_id: str = "",
        flow: str = "",
        sub_id: Optional[str] = None,
        panel_snapshot: Optional[PanelServerSnapshot] = None,
    ) -> Dict[str, Any]:
        return await self._run_with_stale_profile_retry(
            lambda: self._add_client_impl(
                inbound_id=inbound_id,
                email=email,
                total_gb=total_gb,
                expire_days=expire_days,
                limit_ip=limit_ip,
                enable=enable,
                tg_id=tg_id,
                flow=flow,
                sub_id=sub_id,
                panel_snapshot=panel_snapshot,
            )
        )

    async def _add_client_impl(
        self,
        inbound_id: int,
        email: str,
        total_gb: int = 0,
        expire_days: int = 30,
        limit_ip: int = 1,
        enable: bool = True,
        tg_id: str = "",
        flow: str = "",
        sub_id: Optional[str] = None,
        panel_snapshot: Optional[PanelServerSnapshot] = None,
    ) -> Dict[str, Any]:
        """
        Adds the client to inbound.

        Args:
            inbound_id: ID of the inbound connection
            email: Unique client identifier (use user_{id})
            total_gb: Traffic limit in GB (0 = no limit)
            expire_days: Expiration date in days (0 = unlimited)
            limit_ip: IP limit (1 = 1 device)
            enable: Whether the client is active
            tg_id: Telegram ID for panel notifications
            flow: Flow parameter (e.g. 'xtls-rprx-vision' for VLESS Reality/TLS TCP)
            sub_id: Subscription ID. If transferred, it is used as is (for
                subscription mode, where one subId must be on all clients
                with one email). If None, a new uuid is generated.

        Returns:
            Dictionary with created client data

        Raises:
            ValueError: If expire_days <= 0
        """
        if expire_days <= 0:
            raise ValueError("Срок действия ключа должен быть больше 0 дней")
        profile = await self._ensure_api_profile()
        try:
            add_attempts = max(1, int(RETRY_CONFIG.get("max_attempts", 3)))
        except (TypeError, ValueError):
            add_attempts = 3
        add_delays = RETRY_CONFIG.get("delays", [])

        async def wait_before_next_attempt(attempt: int) -> None:
            if attempt >= add_attempts - 1:
                return
            delay = 0
            if add_delays:
                delay = add_delays[min(attempt, len(add_delays) - 1)]
            if delay:
                await asyncio.sleep(delay)

        # Regular protocols stay compatible with the public list method. If the
        # target is MTProto, it is resolved from the raw panel list because
        # get_inbounds() deliberately hides MTProto from single-key mode.
        inbounds = (
            panel_snapshot.inbounds
            if panel_snapshot is not None
            else await self.get_inbounds(include_ignored=True)
        )
        target_inbound = next(
            (ib for ib in inbounds if ib.get("id") == inbound_id),
            None,
        )
        if target_inbound is None:
            raw_inbounds = await self._get_all_inbounds()
            target_inbound = next(
                (ib for ib in raw_inbounds if ib.get("id") == inbound_id),
                None,
            )
        if target_inbound is None:
            raise VPNAPIError(f"Inbound {inbound_id} не найден в панели")
        if is_ignored_inbound(target_inbound):
            raise VPNAPIError(f"Inbound {inbound_id} исключён из управления префиксом --!")

        protocol = str(target_inbound.get("protocol") or "").strip().lower()
        if protocol == "mtproto" and not self._supports_mtproto_multi_client(profile):
            raise VPNAPIError(
                "Индивидуальные клиенты MTProto поддерживаются только в "
                "3X-UI 3.5.0+ через clients_api"
            )
        settings = self._load_json_field(target_inbound.get("settings", "{}"))
        method = settings.get("method", "") if isinstance(settings, dict) else ""

        client_uuid = str(uuid.uuid4())
        
        # Shadowsocks 2022 requires a base64 password of a certain length
        if protocol == 'shadowsocks':
            import base64
            import os
            if method.startswith('2022-'):
                if '128' in method:
                    client_uuid = base64.b64encode(os.urandom(16)).decode('utf-8')
                else:
                    client_uuid = base64.b64encode(os.urandom(32)).decode('utf-8')
            else:
                # For regular SS it is better to use base64 too (more reliable than uuid with hyphens)
                client_uuid = base64.urlsafe_b64encode(os.urandom(16)).decode('utf-8').rstrip('=')

        # Expiration time (timestamp in ms)
        expire_time = int((time.time() + expire_days * 86400) * 1000) if expire_days > 0 else 0
        
        # Traffic limit (bytes)
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
        
        # Basic client structure
        client_entry = {
            "email": email,
            "limitIp": limit_ip,
            "totalGB": total_bytes,
            "expiryTime": expire_time,
            "enable": enable,
            "tgId": tg_id,
            "subId": sub_id if sub_id else uuid.uuid4().hex,
            "reset": 0,
        }
        
        # Protocol-dependent fields
        if protocol == 'trojan':
            # Trojan uses password instead of id
            client_entry["password"] = client_uuid
            client_entry["flow"] = flow
        elif protocol == 'shadowsocks':
            # Shadowsocks - clients inherit password/method from inbound
            client_entry["password"] = client_uuid
            client_entry["method"] = ""
        else:
            # VLESS / VMess - use id (UUID)
            client_entry["id"] = client_uuid
            client_entry["flow"] = flow
        
        # Structure for 3X-UI
        client_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [client_entry]
            })
        }

        async def finalize_add_result(result: Dict[str, Any]) -> Dict[str, Any]:
            if protocol != "mtproto":
                return result
            verified_client = await self._get_verified_mtproto_client(
                email,
                inbound_id,
                attempts=add_attempts,
            )
            return self._build_add_client_result(
                verified_client,
                inbound_id,
                email,
                result.get("uuid") or client_uuid,
                expire_time,
                total_gb,
                client_entry["subId"],
            )

        if profile == API_PROFILE_CLIENTS:
            snapshot_state = (
                panel_snapshot.get_client(email)
                if panel_snapshot is not None
                else None
            )
            record = (
                {
                    "client": dict(snapshot_state.client),
                    "inboundIds": sorted(snapshot_state.inbound_ids),
                }
                if snapshot_state is not None
                else None
            )
            if panel_snapshot is None:
                record = await self._get_clients_api_record(email)
            if record:
                existing_client, inbound_ids = self._split_clients_api_record(record)
                existing_result = self._build_add_client_result(
                    existing_client,
                    inbound_id,
                    email,
                    client_uuid,
                    expire_time,
                    total_gb,
                    client_entry["subId"],
                )
                if inbound_id not in inbound_ids:
                    encoded_email = urllib.parse.quote(email, safe="")
                    for attempt in range(add_attempts):
                        try:
                            await self._request(
                                "POST",
                                f"/panel/api/clients/{encoded_email}/attach",
                                data={"inboundIds": [inbound_id]},
                                retry=False,
                            )
                            break
                        except VPNAPIError:
                            recovered = await self._recover_added_client(
                                profile,
                                inbound_id,
                                email,
                                client_uuid,
                                expire_time,
                                total_gb,
                                client_entry["subId"],
                            )
                            if recovered:
                                return await finalize_add_result(recovered)
                            if attempt >= add_attempts - 1:
                                raise
                            await wait_before_next_attempt(attempt)
                if (
                    panel_snapshot is None
                    and flow
                    and (existing_client.get("flow") or "") != flow
                ):
                    updated_client = self._build_client_payload_from_record(
                        existing_client,
                        fallback_email=email,
                        fallback_uuid=existing_result["uuid"],
                    )
                    updated_client["email"] = email
                    updated_client["flow"] = flow
                    encoded_email = urllib.parse.quote(email, safe="")
                    for attempt in range(add_attempts):
                        try:
                            await self._request(
                                "POST",
                                f"/panel/api/clients/update/{encoded_email}",
                                data=updated_client,
                                retry=False,
                            )
                            break
                        except VPNAPIError:
                            recovered = await self._recover_added_client(
                                profile,
                                inbound_id,
                                email,
                                client_uuid,
                                expire_time,
                                total_gb,
                                client_entry["subId"],
                            )
                            if recovered:
                                return await finalize_add_result(recovered)
                            if attempt >= add_attempts - 1:
                                raise
                            await wait_before_next_attempt(attempt)
                    existing_client["flow"] = flow
                return await finalize_add_result(existing_result)

            api_client_entry = dict(client_entry)
            api_client_entry["tgId"] = self._normalize_tg_id(tg_id)
            for attempt in range(add_attempts):
                try:
                    await self._request(
                        "POST",
                        "/panel/api/clients/add",
                        data={"client": api_client_entry, "inboundIds": [inbound_id]},
                        retry=False,
                    )
                    break
                except VPNAPIError:
                    recovered = await self._recover_added_client(
                        profile,
                        inbound_id,
                        email,
                        client_uuid,
                        expire_time,
                        total_gb,
                        client_entry["subId"],
                    )
                    if recovered:
                        return await finalize_add_result(recovered)
                    if attempt >= add_attempts - 1:
                        raise
                    await wait_before_next_attempt(attempt)
            created_record = None
            if panel_snapshot is None:
                created_record = await self._get_clients_api_record(
                    email,
                    log_error=False,
                )
            if created_record:
                created_client, _ = self._split_clients_api_record(created_record)
                result = self._build_add_client_result(
                    created_client,
                    inbound_id,
                    email,
                    client_uuid,
                    expire_time,
                    total_gb,
                    client_entry["subId"],
                )
                return await finalize_add_result(result)
        else:
            _, existing_client = self._find_client_in_inbounds(
                inbounds,
                inbound_id=inbound_id,
                email=email,
            )
            if existing_client:
                return self._build_add_client_result(
                    existing_client,
                    inbound_id,
                    email,
                    client_uuid,
                    expire_time,
                    total_gb,
                    client_entry["subId"],
                )

            for attempt in range(add_attempts):
                try:
                    await self._request(
                        "POST",
                        "/panel/api/inbounds/addClient",
                        data=client_data,
                        retry=False,
                    )
                    break
                except VPNAPIError:
                    recovered = await self._recover_added_client(
                        profile,
                        inbound_id,
                        email,
                        client_uuid,
                        expire_time,
                        total_gb,
                        client_entry["subId"],
                    )
                    if recovered:
                        return recovered
                    if attempt >= add_attempts - 1:
                        raise
                    await wait_before_next_attempt(attempt)

        return await finalize_add_result({
            "uuid": client_uuid,
            "email": email,
            "inbound_id": inbound_id,
            "expire_time": expire_time,
            "total_gb": total_gb,
            "sub_id": client_entry["subId"],
        })
    
    async def get_inbound_flow(self, inbound_id: int) -> str:
        """
        Defines the desired flow value for inbound.
        Flow = 'xtls-rprx-vision' is only needed for VLESS + TCP + (Reality or TLS).
        """
        try:
            inbounds = await self.get_inbounds()
            for inbound in inbounds:
                if inbound['id'] == inbound_id:
                    protocol = inbound.get('protocol', '')
                    if protocol != 'vless':
                        return ""
                    
                    stream_raw = inbound.get('streamSettings', '{}')
                    if isinstance(stream_raw, str):
                        stream = json.loads(stream_raw)
                    else:
                        stream = stream_raw
                    
                    network = stream.get('network', 'tcp')
                    security = stream.get('security', 'none')
                    
                    # Flow is only needed for VLESS + TCP + (reality | tls)
                    if network == 'tcp' and security in ('reality', 'tls'):
                        return 'xtls-rprx-vision'
                    return ""
        except Exception as e:
            logger.warning(f"Error determining flow for inbound {inbound_id}: {e}")
        return ""
    
    @staticmethod
    def _traffic_int(value: Any) -> Optional[int]:
        """Normalizes numeric traffic fields from different API versions."""
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_traffic_int(cls, data: Dict[str, Any], fields: tuple, default: int = 0) -> int:
        for field in fields:
            value = cls._traffic_int(data.get(field))
            if value is not None:
                return value
        return default

    @classmethod
    def _traffic_used_from_payload(cls, data: Dict[str, Any]) -> Optional[int]:
        up = cls._traffic_int(data.get("up"))
        down = cls._traffic_int(data.get("down"))
        if up is not None or down is not None:
            return (up or 0) + (down or 0)

        for field in (
            "traffic_used",
            "trafficUsed",
            "usedTraffic",
            "usedBytes",
            "used_bytes",
            "usedGB",
            "used",
            "consumedTraffic",
            "consumed",
        ):
            value = cls._traffic_int(data.get(field))
            if value is not None:
                return value
        return None

    @classmethod
    def _normalize_client_traffic_payload(
        cls,
        payload: Any,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        """Converts /clients/traffic and /clients/get responses to the old get_client_stats() format."""
        if not isinstance(payload, dict):
            return None

        data = dict(payload)
        client_payload = payload.get("client")
        if isinstance(client_payload, dict):
            data.update(client_payload)

        traffic_used = cls._traffic_used_from_payload(data)
        if traffic_used is None:
            return None

        up = cls._traffic_int(data.get("up"))
        down = cls._traffic_int(data.get("down"))
        if up is None and down is None:
            up = traffic_used
            down = 0

        return {
            "up": up or 0,
            "down": down or 0,
            "traffic_used": traffic_used,
            "total": cls._first_traffic_int(
                data,
                ("total", "totalGB", "traffic_limit", "trafficLimit"),
                0,
            ),
            "protocol": data.get("protocol") or "vless",
            "remark": data.get("remark") or "",
            "expiry_time": cls._first_traffic_int(
                data,
                ("expiryTime", "expiry_time", "expire", "expires_at"),
                0,
            ),
            "source": source,
        }

    async def get_client_stats(
        self,
        email: str,
        resolve_inbound: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves traffic statistics and protocol for a specific client.
        
        Args:
            email: Email/client ID
            
        Returns:
            Dictionary with statistics or None:
            - up: Traffic for all time (up) bytes
            - down: Traffic for all time (down) bytes
            - total: Traffic limit (bytes)
            - protocol: Connection protocol (vless, vmess, etc.)
        """
        try:
            profile = await self._ensure_api_profile()
            if profile == API_PROFILE_CLIENTS:
                encoded_email = urllib.parse.quote(email, safe="")
                try:
                    result = await self._request(
                        "GET",
                        f"/panel/api/clients/traffic/{encoded_email}",
                        retry=False,
                        log_error=False,
                    )
                    stats = self._normalize_client_traffic_payload(
                        result.get("obj"),
                        "clients_api_traffic",
                    )
                except VPNAPIError as e:
                    logger.debug(f"/clients/traffic недоступен для {email}: {e}")
                    stats = None

                if not stats:
                    record = await self._get_clients_api_record(email, log_error=False)
                    stats = self._normalize_client_traffic_payload(
                        record,
                        "clients_api_client",
                    )

                if stats:
                    protocol = "vless"
                    remark = ""
                    if resolve_inbound:
                        try:
                            inbound, _ = await self._find_panel_client(email=email)
                            if inbound:
                                protocol = inbound.get("protocol", protocol)
                                remark = inbound.get("remark", "")
                        except Exception:
                            pass
                    stats["protocol"] = protocol
                    stats["remark"] = remark
                    return stats

            inbounds = await self.get_inbounds()
            for inbound in inbounds:
                client_stats = inbound.get("clientStats", [])
                for stats in client_stats:
                    if stats.get("email") == email:
                        up = stats.get("up", 0) or 0
                        down = stats.get("down", 0) or 0
                        return {
                            "up": up,
                            "down": down,
                            "traffic_used": up + down,
                            "total": stats.get("total", 0),
                            "protocol": inbound.get("protocol", "vless"),
                            "remark": inbound.get("remark", ""),
                            "expiry_time": stats.get("expiryTime", 0),
                            "source": "inbound_first",
                        }
        except Exception as e:
            logger.warning(f"Ошибка получения статистики клиента {email}: {e}")
        return None
    
    async def delete_client(
        self,
        inbound_id: int,
        client_uuid: str,
        *,
        panel_state: Optional[PanelClientState] = None,
    ) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._delete_client_impl(
                inbound_id,
                client_uuid,
                panel_state=panel_state,
            )
        )

    async def _delete_client_impl(
        self,
        inbound_id: int,
        client_uuid: str,
        *,
        panel_state: Optional[PanelClientState] = None,
    ) -> bool:
        """
        Removes a client from inbound.

        Args:
            inbound_id: ID of the inbound connection
            client_uuid: Client UUID

        Returns:
            True if deletion was successful
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            if panel_state is not None:
                email = panel_state.email or client_uuid
                inbound_ids = set(panel_state.inbound_ids)
            else:
                _, client = await self._find_panel_client(
                    inbound_id=inbound_id,
                    client_uuid=client_uuid,
                    include_ignored=True,
                )
                email = client.get("email") if isinstance(client, dict) else None
                if not email:
                    email = client_uuid
                record = await self._get_clients_api_record(email, log_error=False)
                inbound_ids = set()
                if record:
                    _, record_inbound_ids = self._split_clients_api_record(record)
                    inbound_ids = set(record_inbound_ids)
            encoded_email = urllib.parse.quote(email, safe="")
            if len(inbound_ids) > 1:
                await self._request(
                    "POST",
                    f"/panel/api/clients/{encoded_email}/detach",
                    data={"inboundIds": [inbound_id]},
                )
                return True
            await self._request("POST", f"/panel/api/clients/del/{encoded_email}")
            return True

        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{encoded_uuid}")
        return True

    async def delete_clients_by_email_on_server(self, email: str) -> int:
        return await self._run_with_stale_profile_retry(
            lambda: self._delete_clients_by_email_on_server_impl(email)
        )

    async def _delete_clients_by_email_on_server_impl(self, email: str) -> int:
        """
        Removes ALL clients with the specified email from all inbound servers.

        Used in subscription mode when replacing a key or deleting a key
        and when switching to keys mode (clearing duplicates).

        Args:
            email: Email/client ID

        Returns:
            Number of actually deleted clients
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            record = await self._get_clients_api_record(email, log_error=False)
            if not record:
                return 0
            _, inbound_ids = self._split_clients_api_record(record)
            encoded_email = urllib.parse.quote(email, safe="")
            await self._request("POST", f"/panel/api/clients/del/{encoded_email}")
            return max(1, len(inbound_ids))

        inbounds = await self.get_inbounds(include_ignored=True)
        deleted = 0
        for inbound in inbounds:
            try:
                settings = self._load_json_field(inbound.get('settings', '{}'))
            except TypeError:
                continue
            for client in settings.get('clients', []):
                if client.get('email') != email:
                    continue
                cid = client.get('id') or client.get('password')
                if not cid:
                    continue
                try:
                    await self.delete_client(inbound['id'], cid)
                    deleted += 1
                except VPNAPIError as e:
                    logger.warning(
                        f"Не удалось удалить клиента {email} из inbound {inbound['id']}: {e}"
                    )
        return deleted

    async def set_clients_enabled_by_email(self, email: str, enable: bool) -> int:
        return await self._run_with_stale_profile_retry(
            lambda: self._set_clients_enabled_by_email_impl(email, enable)
        )

    async def _set_clients_enabled_by_email_impl(self, email: str, enable: bool) -> int:
        """
        Enables/disables ALL clients with the specified email in all inbound servers.

        Used when traffic expires or expires in subscription mode:
        the panel itself does not disconnect the client according to our counter (only according to its totalGB),
        so we turn it off manually.

        Args:
            email: Client email
            enable: True - enable, False - disable

        Returns:
            Number of updated clients
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            record = await self._get_clients_api_record(email, log_error=False)
            if not record:
                return 0
            client, inbound_ids = self._split_clients_api_record(record)
            if client.get("enable", True) == enable:
                return 0
            payload = self._build_client_payload_from_record(client, fallback_email=email)
            payload["enable"] = enable
            payload["reset"] = 0
            encoded_email = urllib.parse.quote(email, safe="")
            await self._request("POST", f"/panel/api/clients/update/{encoded_email}", data=payload)
            return max(1, len(inbound_ids))

        inbounds = await self.get_inbounds()
        count = 0
        for inbound in inbounds:
            try:
                settings = self._load_json_field(inbound.get('settings', '{}'))
            except TypeError:
                continue
            for cl in settings.get('clients', []):
                if cl.get('email') != email:
                    continue
                if cl.get('enable', True) == enable:
                    continue
                cid = cl.get('id') or cl.get('password')
                if not cid:
                    continue
                # We save all client fields, change only enable
                updated_client = dict(cl)
                updated_client['enable'] = enable
                data = {
                    "id": inbound['id'],
                    "settings": json.dumps({"clients": [updated_client]}),
                }
                try:
                    encoded = urllib.parse.quote(cid, safe='')
                    await self._request(
                        "POST",
                        f"/panel/api/inbounds/updateClient/{encoded}",
                        data=data,
                    )
                    count += 1
                except VPNAPIError as e:
                    action = "включить" if enable else "отключить"
                    logger.warning(
                        f"Не удалось {action} клиента {email} в inbound {inbound['id']}: {e}"
                    )
        return count

    async def get_panel_settings(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Gets panel settings via setting/all.

        In particular, it returns the subscription server fields: subEnable, subListen,
        subPort, subPath, subDomain, subURI, subCertFile, subKeyFile, subEncrypt.

        The result is cached at the client level; repeated calls do not make requests.

        Args:
            force_refresh: True - ignore the cache and make a new request.

        Returns:
            Settings dictionary or None if getting failed.
        """
        if not force_refresh and self._panel_settings is not None:
            return self._panel_settings

        for endpoint in self._setting_endpoints("all"):
            try:
                resp = await self._request(
                    "POST",
                    endpoint,
                    retry=False,
                    log_error=False,
                )
            except Exception as e:
                logger.debug(f"get_panel_settings: {endpoint} не сработал: {e}")
                continue
            if not isinstance(resp, dict) or not resp.get("success"):
                logger.debug(f"get_panel_settings: {endpoint} ответил без success: {resp}")
                continue
            obj = resp.get("obj")
            if not isinstance(obj, dict):
                logger.debug(f"get_panel_settings: {endpoint} вернул obj не dict: {obj}")
                continue
            self._panel_settings = obj
            return obj

        logger.warning(
            f"get_panel_settings: не удалось получить настройки панели "
            f"{self.server.get('name', self.server_id)} через известные setting endpoints"
        )
        return None

    async def build_subscription_url(self, sub_id: str) -> Optional[str]:
        """
        Returns the HTTP subscription URL for the user, collected by settings
        subscription-server from the API panel (subDomain/subPort/subPath/subURI).

        DOES NOT guess the port/path using the host:port API - takes real values from the panel.
        If the panel has subEnable=false or the settings could not be obtained, it returns
        None: Let the calling code show the user a meaningful error rather than
        will show a broken URL.

        Args:
            sub_id: Subscription ID of the client

        Returns:
            Full URL like 'https://host:2096/sub/{sub_id}'' or None.
        """
        settings = await self.get_panel_settings()
        if not settings:
            logger.warning(
                f"build_subscription_url: не удалось получить настройки панели "
                f"{self.server.get('name', self.server_id)}; URL не строится."
            )
            return None

        # Is the subscription even included?
        if not settings.get("subEnable"):
            logger.warning(
                f"build_subscription_url: на панели {self.server.get('name', self.server_id)} "
                f"subscription отключена (subEnable=false). Включите её в настройках 3X-UI."
            )
            return None

        # If the admin has set a custom subURI - this is a ready-made prefix, we add only the sub_id.
        sub_uri = (settings.get("subURI") or "").strip()
        if sub_uri:
            if not sub_uri.endswith("/"):
                sub_uri = sub_uri + "/"
            return f"{sub_uri}{sub_id}"

        # We collect URLs from components.
        from urllib.parse import urlparse
        sub_domain = (settings.get("subDomain") or "").strip()
        if not sub_domain:
            # Take the panel host (without http://)
            parsed = urlparse(self.base_url)
            sub_domain = parsed.hostname or self.host

        sub_port = settings.get("subPort") or 0
        try:
            sub_port = int(sub_port)
        except (TypeError, ValueError):
            sub_port = 0

        # Path: 3X-UI puts it as '/sub/' or 'sub/' - let's normalize it.
        sub_path = settings.get("subPath") or "/"
        if not sub_path.startswith("/"):
            sub_path = "/" + sub_path
        if not sub_path.endswith("/"):
            sub_path = sub_path + "/"

        # Scheme: HTTPS if the sub-server has a certificate, otherwise HTTP.
        # subKeyFile + subCertFile together means TLS on the sub-port.
        cert_file = (settings.get("subCertFile") or "").strip()
        key_file = (settings.get("subKeyFile") or "").strip()
        scheme = "https" if (cert_file and key_file) else "http"

        port_part = f":{sub_port}" if sub_port and sub_port not in (80 if scheme == "http" else 443,) else ""
        return f"{scheme}://{sub_domain}{port_part}{sub_path}{sub_id}"


    async def update_client_traffic_limit(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        total_gb: int
    ) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._update_client_traffic_limit_impl(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                total_gb=total_gb,
            )
        )

    async def _update_client_traffic_limit_impl(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        total_gb: int
    ) -> bool:
        """
        Updates the traffic limit of an existing client.
        
        Args:
            inbound_id: ID of the inbound connection
            client_uuid: Client UUID
            email: Email/client ID
            total_gb: New traffic limit in GB (0 = no limit)
            
        Returns:
            True if update is successful
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            return await self.update_client_limit(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                total_gb_bytes=total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0,
            )

        # We receive current client data
        inbounds = await self.get_inbounds()
        target_inbound = None
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                target_inbound = inbound
                settings = self._load_json_field(inbound.get('settings', '{}'))
                clients = settings.get('clients', [])
                
                for client in clients:
                    if client.get('id') == client_uuid:
                        target_client = client
                        break
                break
        
        if not target_inbound or not target_client:
            raise VPNAPIError(f"Клиент {email} не найден в inbound {inbound_id}")
        
        # Updating the traffic limit
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
        target_client['totalGB'] = total_bytes
        
        # Generating data for updating
        update_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": target_client.get('id'),
                    "email": target_client.get('email'),
                    "limitIp": target_client.get('limitIp', 1),
                    "totalGB": total_bytes,
                    "expiryTime": target_client.get('expiryTime', 0),
                    "enable": target_client.get('enable', True),
                    "tgId": target_client.get('tgId', ''),
                    "subId": target_client.get('subId', ''),
                    "reset": target_client.get('reset', 0)
                }]
            })
        }
        
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        logger.info(f"Обновлен лимит трафика клиента {email}: {total_gb} ГБ")
        return True

    async def disable_reset_for_all_clients(self) -> int:
        return await self._run_with_stale_profile_retry(
            self._disable_reset_for_all_clients_impl
        )

    async def _disable_reset_for_all_clients_impl(self) -> int:
        """
        Disables auto-renewal (traffic/day reset) on the 1st of the month for all clients.
        Sets the reset field to 0 for all clients in all inbounds.
        
        Returns:
            Number of clients updated.
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            updated_count = 0
            try:
                result = await self._request("GET", "/panel/api/clients/list", retry=False, log_error=False)
                rows = result.get("obj") or []
            except Exception:
                rows = []
            if not isinstance(rows, list):
                return 0
            for row in rows:
                if not isinstance(row, dict) or row.get("reset", 0) == 0:
                    continue
                email = row.get("email")
                if not email:
                    continue
                payload = self._build_client_payload_from_record(row, fallback_email=email)
                payload["reset"] = 0
                try:
                    encoded_email = urllib.parse.quote(email, safe="")
                    await self._request(
                        "POST",
                        f"/panel/api/clients/update/{encoded_email}",
                        data=payload,
                    )
                    updated_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при отключении автопродления для клиента {email}: {e}")
            return updated_count

        updated_count = 0
        inbounds = await self.get_inbounds()
        
        for inbound in inbounds:
            settings = self._load_json_field(inbound.get('settings', '{}'))
            clients = settings.get('clients', [])
            
            for client in clients:
                if client.get('reset', 0) != 0:  # only if reset is not 0
                    
                    # clientId is id(uuid) for vless/vmess, password for trojan/shadowsocks
                    client_id = client.get('id') or client.get('password')
                    
                    if client_id:
                        # We form the correct client structure for updating, saving the necessary fields
                        updated_client = {
                            "id": client.get('id', ''),
                            "password": client.get('password', ''),
                            "flow": client.get('flow', ''),
                            "email": client.get('email', ''),
                            "limitIp": client.get('limitIp', 1),
                            "totalGB": client.get('totalGB', 0),
                            "expiryTime": client.get('expiryTime', 0),
                            "enable": client.get('enable', True),
                            "tgId": client.get('tgId', ''),
                            "subId": client.get('subId', ''),
                            "reset": 0  # Reset
                        }
                        
                        # Removing empty fields (important for different protocols)
                        updated_client = {k: v for k, v in updated_client.items() if v != ''}
                        
                        client_data = {
                            "id": inbound['id'],
                            "settings": json.dumps({"clients": [updated_client]})
                        }
                        
                        try:
                            # In 3x-ui we send POST /panel/api/inbounds/updateClient/:clientId
                            # And in the body of the request we pass the inbound id and a new clients object
                            # We encode the ID/password for the URL so that slashes in base64 (Shadowsocks) do not break HTTP routing
                            encoded_id = urllib.parse.quote(client_id, safe='')
                            await self._request(
                                "POST",
                                f"/panel/api/inbounds/updateClient/{encoded_id}",
                                data=client_data
                            )
                            updated_count += 1
                            logger.info(f"Отключено автопродление (reset=0) для клиента {client.get('email', client_id)}")
                        except StaleAPIProfileError:
                            raise
                        except Exception as e:
                            logger.error(f"Ошибка при отключении автопродления для клиента {client.get('email', client_id)}: {e}")
                            
        return updated_count

    async def update_client_full(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        expiry_time_ms: int,
        total_gb_bytes: int,
        enable: Optional[bool] = None,
        sub_id: Optional[str] = None,
        limit_ip: Optional[int] = None,
        flow: Optional[str] = None,
        panel_client: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._update_client_full_impl(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                expiry_time_ms=expiry_time_ms,
                total_gb_bytes=total_gb_bytes,
                enable=enable,
                sub_id=sub_id,
                limit_ip=limit_ip,
                flow=flow,
                panel_client=panel_client,
            )
        )

    async def _update_client_full_impl(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        expiry_time_ms: int,
        total_gb_bytes: int,
        enable: Optional[bool] = None,
        sub_id: Optional[str] = None,
        limit_ip: Optional[int] = None,
        flow: Optional[str] = None,
        panel_client: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Updates ALL client parameters on the panel with data from our database.
        The only recording function on the panel (except for creating/deleting).
        
        Protocol fields (flow, tgId) are read from the panel,
        but expiryTime, totalGB, enable, subId and limitIp are taken when passed explicitly
        from parameters (from our database).
        
        Args:
            inbound_id: ID of the inbound connection
            client_uuid: Client UUID
            email: Email/client ID
            expiry_time_ms: Expiration time in milliseconds (from our database, 0 = never expire)
            total_gb_bytes: Traffic limit in bytes (from our database, 0 = unlimited)
            enable: Explicit client status. None = save current panel value
            sub_id: Explicit subscription ID. None = save current panel value
            limit_ip: Explicit device limit. None = save current panel value
            flow: Explicit flow. None = save current panel value
            
        Returns:
            True if update is successful
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            target_client = dict(panel_client) if panel_client else None
            if target_client is None:
                record = await self._get_clients_api_record(email, log_error=False)
                if record:
                    target_client, _ = self._split_clients_api_record(record)
            if target_client is None:
                _, target_client = await self._find_panel_client(
                    inbound_id=inbound_id,
                    client_uuid=client_uuid,
                    email=email,
                )
            if not target_client:
                raise VPNAPIError(f"Клиент {email} не найден в clients API")

            updated_client = self._build_client_payload_from_record(
                target_client,
                fallback_email=email,
                fallback_uuid=client_uuid,
            )
            updated_client["email"] = email
            updated_client["totalGB"] = total_gb_bytes
            updated_client["expiryTime"] = expiry_time_ms
            updated_client["enable"] = updated_client.get("enable", True) if enable is None else enable
            updated_client["subId"] = updated_client.get("subId", "") if sub_id is None else sub_id
            updated_client["limitIp"] = updated_client.get("limitIp", 1) if limit_ip is None else limit_ip
            updated_client["reset"] = 0
            if flow is not None:
                updated_client["flow"] = flow

            encoded_email = urllib.parse.quote(email, safe="")
            await self._request(
                "POST",
                f"/panel/api/clients/update/{encoded_email}",
                data=updated_client,
            )

            from datetime import datetime
            expiry_str = datetime.fromtimestamp(expiry_time_ms / 1000).strftime('%Y-%m-%d %H:%M') if expiry_time_ms > 0 else '∞'
            limit_str = f"{total_gb_bytes / 1024**3:.1f} ГБ" if total_gb_bytes > 0 else '∞'
            logger.info(
                f"Обновлён клиент {email} через clients API: expiry={expiry_str}, "
                f"limit={limit_str}, enable={updated_client.get('enable')}"
            )
            return True

        # Reading current client data is unnecessary when a batch snapshot
        # already supplied the exact placement being changed.
        target_client = dict(panel_client) if panel_client else None
        if target_client is None:
            inbounds = await self.get_inbounds()
            for inbound in inbounds:
                if inbound.get('id') == inbound_id:
                    settings = self._load_json_field(inbound.get('settings', '{}'))
                    clients = settings.get('clients', [])

                    for client in clients:
                        if client.get('id') == client_uuid or client.get('password') == client_uuid:
                            target_client = client
                            break
                    break
        
        if not target_client:
            raise VPNAPIError(f"Клиент {email} не найден в inbound {inbound_id}")
        
        # We generate data: expiryTime and totalGB from PARAMETERS (our database),
        # the rest is from the client’s current data on the panel
        updated_client = {
            "id": target_client.get('id', ''),
            "password": target_client.get('password', ''),
            "flow": target_client.get('flow', '') if flow is None else flow,
            "email": target_client.get('email', email),
            "limitIp": target_client.get('limitIp', 1) if limit_ip is None else limit_ip,
            "totalGB": total_gb_bytes,          # ← From our database!
            "expiryTime": expiry_time_ms,        # ← From our database!
            "enable": target_client.get('enable', True) if enable is None else enable,
            "tgId": target_client.get('tgId', ''),
            "subId": target_client.get('subId', '') if sub_id is None else sub_id,
            "reset": 0  # We do not use auto-reset panels
        }
        
        # Removing empty string fields (for different protocols)
        updated_client = {k: v for k, v in updated_client.items() if v != ''}
        
        update_data = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [updated_client]})
        }
        
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        
        from datetime import datetime
        expiry_str = datetime.fromtimestamp(expiry_time_ms / 1000).strftime('%Y-%m-%d %H:%M') if expiry_time_ms > 0 else '∞'
        limit_str = f"{total_gb_bytes / 1024**3:.1f} ГБ" if total_gb_bytes > 0 else '∞'
        logger.info(
            f"Обновлён клиент {email}: expiry={expiry_str}, "
            f"limit={limit_str}, enable={updated_client.get('enable')}"
        )
        return True

    async def extend_client_expiry(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        days: int
    ) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._extend_client_expiry_impl(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                days=days,
            )
        )

    async def _extend_client_expiry_impl(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        days: int
    ) -> bool:
        """
        Extends the client's validity period by the specified number of days.
        If the time limit has already expired, adds days to the current time.
        
        Args:
            inbound_id: ID of the inbound connection
            client_uuid: Client UUID
            email: Email/client ID
            days: Number of days to extend
            
        Returns:
            True if update is successful
        """
        import time

        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            record = await self._get_clients_api_record(email, log_error=False)
            target_client = None
            if record:
                target_client, _ = self._split_clients_api_record(record)
            if not target_client:
                _, target_client = await self._find_panel_client(
                    inbound_id=inbound_id,
                    client_uuid=client_uuid,
                    email=email,
                )
            if not target_client:
                raise VPNAPIError(f"Клиент {email} не найден в clients API")

            current_time_ms = int(time.time() * 1000)
            current_expiry = target_client.get('expiryTime', 0)
            extension_ms = days * 86400 * 1000
            if current_expiry == 0:
                new_expiry = 0
            elif current_expiry < current_time_ms:
                new_expiry = current_time_ms + extension_ms
            else:
                new_expiry = current_expiry + extension_ms

            await self.update_client_full(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                expiry_time_ms=new_expiry,
                total_gb_bytes=target_client.get('totalGB', 0),
                enable=target_client.get('enable', True),
                sub_id=target_client.get('subId', ''),
            )
            logger.info(f"Продлен ключ клиента {email} через clients API на {days} дней. Новый expiry: {new_expiry}")
            return True
        
        # We receive current client data
        inbounds = await self.get_inbounds()
        target_inbound = None
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                target_inbound = inbound
                settings = self._load_json_field(inbound.get('settings', '{}'))
                clients = settings.get('clients', [])
                
                for client in clients:
                    if client.get('id') == client_uuid or client.get('password') == client_uuid:
                        target_client = client
                        break
                break
                
        if not target_inbound or not target_client:
            raise VPNAPIError(f"Клиент {email} не найден в inbound {inbound_id}")
            
        current_time_ms = int(time.time() * 1000)
        current_expiry = target_client.get('expiryTime', 0)
        
        # Calculation of new expiration time
        extension_ms = days * 86400 * 1000
        if current_expiry == 0:
            # The infinite key remains infinite
            new_expiry = 0
        elif current_expiry < current_time_ms:
            # If the key has already expired, add it to the current moment
            new_expiry = current_time_ms + extension_ms
        else:
            # If still active, add to the current expiration date
            new_expiry = current_expiry + extension_ms
            
        target_client['expiryTime'] = new_expiry
        
        # Generating data for updating
        update_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": target_client.get('id', ''),
                    "password": target_client.get('password', ''),
                    "flow": target_client.get('flow', ''),
                    "email": target_client.get('email', ''),
                    "limitIp": target_client.get('limitIp', 1),
                    "totalGB": target_client.get('totalGB', 0),
                    "expiryTime": new_expiry,
                    "enable": target_client.get('enable', True),
                    "tgId": target_client.get('tgId', ''),
                    "subId": target_client.get('subId', ''),
                    "reset": target_client.get('reset', 0)
                }]
            })
        }
        
        # Removing empty fields (important for different protocols, where id or password may be missing)
        clients_array = json.loads(update_data["settings"])["clients"][0]
        clients_array = {k: v for k, v in clients_array.items() if v != ''}
        update_data["settings"] = json.dumps({"clients": [clients_array]})
        
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        logger.info(f"Продлен ключ клиента {email} на {days} дней. Новый expiry: {new_expiry}")
        return True

    async def get_client_config(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the complete client configuration for the connection.
        
        Args:
            email: Email/client ID
            
        Returns:
            Dictionary with connection settings or None
        """
        try:
            inbounds = await self.get_inbounds()
            for inbound in inbounds:
                settings = self._load_json_field(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                
                target_client = None
                for client in clients:
                    if client.get("email") == email:
                        target_client = client
                        break
                
                if target_client:
                    # We found the client, return the configuration
                    stream_settings = self._load_json_field(inbound.get("streamSettings", "{}"))
                    protocol = inbound.get("protocol", "vless")
                    
                    # DEBUG: logging stream_settings to debug Reality parameters
                    logger.debug(f"Stream settings for {email}: {json.dumps(stream_settings, ensure_ascii=False)}")
                    if stream_settings.get("security") == "reality":
                        reality = stream_settings.get("realitySettings", {})
                        logger.info(f"Reality settings for {email}: pbk={reality.get('publicKey')}, sni={reality.get('serverName')}, fp={reality.get('fingerprint')}, shortIds={reality.get('shortIds')}")
                    
                    result = {
                        "uuid": target_client.get("id", ""),
                        "email": target_client.get("email", ""),
                        "port": inbound["port"],
                        "protocol": protocol,
                        "host": self.server["host"],
                        "stream_settings": stream_settings,
                        "inbound_name": inbound.get("remark", "VPN"),
                        "sub_id": target_client.get("subId", ""),
                        "flow": target_client.get("flow", "")
                    }
                    
                    # Protocol-specific fields
                    if protocol == 'trojan':
                        result["password"] = target_client.get("password", target_client.get("id", ""))
                    elif protocol == 'shadowsocks':
                        # For Shadowsocks method is stored in inbound settings,
                        # and each client has its own password (from fallback to general)
                        result["method"] = settings.get("method", "aes-256-gcm")
                        result["password"] = target_client.get("password", settings.get("password", ""))
                        result["server_password"] = settings.get("password", "")
                    elif protocol == 'vmess':
                        result["security_method"] = target_client.get("security", "auto")
                    
                    return result
        except Exception as e:
            logger.error(f"Error getting client config for {email}: {e}")
        return None

    async def get_subscription_link(self, sub_id: str) -> Optional[str]:
        """
        Receives a VLESS link through the subscription endpoint.
        
        Args:
            sub_id: Subscription ID of the client
            
        Returns:
            Ready VLESS link or None if it was not possible to receive
        """
        try:
            profile = await self._ensure_api_profile()
            if profile == API_PROFILE_CLIENTS:
                encoded_sub_id = urllib.parse.quote(sub_id, safe="")
                result = await self._request(
                    "GET",
                    f"/panel/api/clients/subLinks/{encoded_sub_id}",
                    retry=False,
                    log_error=False,
                )
                links = result.get("obj")
                if isinstance(links, list):
                    clean_links = [str(link).strip() for link in links if str(link).strip()]
                    if clean_links:
                        return "\n".join(clean_links)
                if isinstance(links, str) and links.strip():
                    return links.strip()
        except Exception as e:
            logger.debug(f"clients API subLinks не сработал для {sub_id}: {e}")

        session = await self._ensure_session()
        
        # Building a list of candidate URLs
        # 1. With base_path
        # 2. Without base_path
        # 3. /subscribe/ instead of /sub/ (sometimes it happens)
        
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        host_url = f"{parsed.scheme}://{parsed.netloc}"
        
        candidates = [
            f"{self.base_url}/sub/{sub_id}",
            f"{host_url}/sub/{sub_id}",
            f"{self.base_url}/subscribe/{sub_id}",
            f"{host_url}/subscribe/{sub_id}"
        ]
        
        for url in candidates:
            try:
                # Important: We do not use _request, since this is a public endpoint
                async with session.get(url, ssl=False) as response:
                    logger.info(f"Sub URL probe: {url} -> {response.status}")

                    if response.status == 200:
                        text = await response.text()
                        text = text.strip()

                        # If returned VLESS
                        if text.startswith("vless://") or text.startswith("vmess://") or text.startswith("trojan://"):
                            return text

                        # If returned base64
                        try:
                            import base64
                            # Add padding if necessary
                            missing_padding = len(text) % 4
                            if missing_padding:
                                text += '=' * (4 - missing_padding)
                            decoded = base64.b64decode(text).decode('utf-8').strip()
                            if decoded.startswith("vless://") or decoded.startswith("vmess://") or decoded.startswith("trojan://"):
                                return decoded
                        except:
                            # Log it if it's something strange
                            if len(text) < 200:
                                logger.debug(f"Unknown response text: {text}")
                            pass
            except Exception as e:
                logger.warning(f"Ошибка получения подписки ({url}): {e}")

        return None

    @staticmethod
    def _detect_database_backup(data: bytes) -> Optional[PanelDatabaseBackup]:
        """Determines the format of the 3X-UI backup file by signature."""
        if data.startswith(b'SQLite format 3\x00'):
            return PanelDatabaseBackup(data=data, extension=".db", db_kind="sqlite")
        if data.startswith(b'PGDMP'):
            return PanelDatabaseBackup(data=data, extension=".dump", db_kind="postgres")
        return None

    async def get_database_backup(self) -> PanelDatabaseBackup:
        """
        Downloads a backup copy of the panel database.
        
        Endpoint: GET /panel/api/server/getDb (or fallbacks)
        
        Returns:
            PanelDatabaseBackup with file bytes, extension and database type
            
        Raises:
            VPNAPIError: There was a download error
        """
        session = await self._ensure_session()

        # Log in if necessary
        if not self.is_authenticated:
            await self.login()

        headers = {
            "Accept": "application/octet-stream",
            "X-Requested-With": "XMLHttpRequest"
        }
        # On v3.0+ via Bearer - bypass the need for cookies + CSRF
        if self.panel_mode == 'bearer' and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        # Different versions of X-UI / 3X-UI use different paths to download the database
        endpoints = [
            "/panel/api/server/getDb",
            "/panel/setting/getDb",
            "/panel/api/getDb",
            "/server/getDb"
        ]
        if self._uses_api_token_only():
            endpoints = [endpoint for endpoint in endpoints if endpoint.startswith("/panel/api/")]
        
        last_status = None
        last_response_preview = ""
        for endpoint in endpoints:
            url = f"{self.base_url}{endpoint}"
            endpoint_headers = dict(headers)
            if endpoint.startswith("/panel/setting/"):
                await self._ensure_cookie_auth()
                session = await self._ensure_session()
                endpoint_headers = {
                    "Accept": "application/octet-stream",
                    "X-Requested-With": "XMLHttpRequest",
                }
                if self.csrf_token:
                    endpoint_headers["X-CSRF-Token"] = self.csrf_token
            try:
                async with session.get(url, headers=endpoint_headers) as response:
                    last_status = response.status
                    if response.status == 200:
                        data = await response.read()
                        backup = self._detect_database_backup(data)
                        if backup:
                            logger.info(
                                f"Скачан бэкап БД панели ({endpoint}, {backup.db_kind}): "
                                f"{len(data)} байт"
                            )
                            return backup

                        last_response_preview = data[:160].decode(errors='ignore').strip()
                        logger.debug(
                            f"Endpoint {endpoint} вернул не backup-файл панели, а: "
                            f"{last_response_preview}..."
                        )
                    else:
                        data = await response.read()
                        if data:
                            last_response_preview = data[:160].decode(errors='ignore').strip()
            except aiohttp.ClientError as e:
                logger.debug(f"Ошибка HTTP при проверке {endpoint}: {e}")

        details = f" Последний HTTP статус: {last_status}."
        if last_response_preview:
            details += f" Ответ панели: {last_response_preview[:120]}"
        raise VPNAPIError(
            "Ошибка скачивания бэкапа панели: ни один endpoint не вернул "
            "SQLite .db или PostgreSQL .dump. Для PostgreSQL нужен 3X-UI v3.2.5+ "
            "и установленный postgresql-client/pg_dump на сервере панели."
            f"{details}"
        )

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._reset_client_traffic_impl(inbound_id, email)
        )

    async def _reset_client_traffic_impl(self, inbound_id: int, email: str) -> bool:
        """
        Resets the client's traffic counters (up/down) on the panel.
        
        Endpoint: POST /panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}
        
        Args:
            inbound_id: ID of the inbound connection
            email: Email/client ID
            
        Returns:
            True on successful reset
        """
        profile = await self._ensure_api_profile()
        encoded_email = urllib.parse.quote(email, safe='')
        if profile == API_PROFILE_CLIENTS:
            await self._request("POST", f"/panel/api/clients/resetTraffic/{encoded_email}")
        else:
            await self._request(
                "POST",
                f"/panel/api/inbounds/{inbound_id}/resetClientTraffic/{encoded_email}"
            )
        logger.info(f"Сброшен трафик клиента {email} (inbound {inbound_id})")
        return True

    async def update_client_limit(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        total_gb_bytes: int
    ) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._update_client_limit_impl(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                total_gb_bytes=total_gb_bytes,
            )
        )

    async def _update_client_limit_impl(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        total_gb_bytes: int
    ) -> bool:
        """
        Updates the client's traffic limit (totalGB) on the panel.
        
        Args:
            inbound_id: ID of the inbound connection
            client_uuid: Client UUID
            email: Email/client ID
            total_gb_bytes: New limit in bytes
            
        Returns:
            True if update is successful
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            record = await self._get_clients_api_record(email, log_error=False)
            target_client = None
            if record:
                target_client, _ = self._split_clients_api_record(record)
            if not target_client:
                _, target_client = await self._find_panel_client(
                    inbound_id=inbound_id,
                    client_uuid=client_uuid,
                    email=email,
                )
            if not target_client:
                raise VPNAPIError(f"Клиент {email} не найден в clients API")

            return await self.update_client_full(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                expiry_time_ms=target_client.get('expiryTime', 0),
                total_gb_bytes=total_gb_bytes,
                enable=target_client.get('enable', True),
                sub_id=target_client.get('subId', ''),
            )

        # We receive current client data
        inbounds = await self.get_inbounds()
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                settings = self._load_json_field(inbound.get('settings', '{}'))
                clients = settings.get('clients', [])
                
                for client in clients:
                    if client.get('id') == client_uuid or client.get('password') == client_uuid:
                        target_client = client
                        break
                break
        
        if not target_client:
            raise VPNAPIError(f"Клиент {email} не найден в inbound {inbound_id}")
        
        # Update totalGB
        target_client['totalGB'] = total_gb_bytes
        
        # Generating data for updating
        updated_client = {
            "id": target_client.get('id', ''),
            "password": target_client.get('password', ''),
            "flow": target_client.get('flow', ''),
            "email": target_client.get('email', ''),
            "limitIp": target_client.get('limitIp', 1),
            "totalGB": total_gb_bytes,
            "expiryTime": target_client.get('expiryTime', 0),
            "enable": target_client.get('enable', True),
            "tgId": target_client.get('tgId', ''),
            "subId": target_client.get('subId', ''),
            "reset": target_client.get('reset', 0)
        }
        
        # Removing empty string fields (important for different protocols)
        updated_client = {k: v for k, v in updated_client.items() if v != ''}
        
        update_data = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [updated_client]})
        }
        
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        
        limit_gb = total_gb_bytes / (1024**3)
        logger.info(f"Обновлён лимит клиента {email}: {limit_gb:.1f} ГБ")
        return True

    async def close(self):
        """Closes the session."""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.is_authenticated = False
        self.cookie_authenticated = False
        self.csrf_token = None


# ============================================================================
# Global Client Cache and Helper Functions
# ============================================================================
