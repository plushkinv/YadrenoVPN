"""
Сервис для работы с API 3X-UI панели.

Обеспечивает:
- Авторизацию через сессии
- Управление клиентами (создание, удаление, обновление)
- Получение статистики трафика
- Управление inbound-подключениями
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


from .base import BaseVPNClient, PanelDatabaseBackup, VPNAPIError
from bot.utils.inbounds import filter_visible_inbounds


class StaleAPIProfileError(Exception):
    """Панель сменила профиль API; операцию нужно выбрать заново."""


class TransientPanelError(VPNAPIError):
    """Временная сетевая недоступность панели."""


class XUIClient(BaseVPNClient):
    """
    Клиент для работы с API 3X-UI панели.
    
    Использует сессионную аутентификацию (cookie-based).
    ВАЖНО: Для 3X-UI куки могут быть привязаны к IP, поэтому используем unsafe=True для CookieJar.
    """
    
    def __init__(self, server: dict):
        """
        Инициализация клиента.

        Args:
            server: Словарь с данными сервера из БД
        """
        self.server = server
        self.server_id = server.get('id')
        self.host = server['host']
        self.port = server['port']
        self.protocol = server.get('protocol', 'https')
        # Гарантируем, что путь начинается со слеша, но НЕ заканчивается им
        # strip('/') убирает слеши и с начала, и с конца
        path = server.get('web_base_path', '').strip('/')
        # Теперь добавляем один слеш в начало (если путь не пустой)
        path = f"/{path}" if path else ""

        self.base_url = f"{self.protocol}://{self.host}:{self.port}{path}"

        self.session: Optional[aiohttp.ClientSession] = None
        self.is_authenticated = False

        # Поддержка разных поколений 3x-ui.
        # auth_mode/panel_mode:
        #   legacy = v2.x cookie; csrf = v3.0+ cookie + X-CSRF-Token;
        #   bearer = v3.0+ через Authorization: Bearer для /panel/api/*.
        # api_profile:
        #   legacy_inbounds = старые client-операции через /panel/api/inbounds/*
        #   clients_api = first-class clients API из 3x-ui v3.1.0+.
        self.panel_mode: Optional[str] = None
        self.auth_mode: Optional[str] = None
        self.cookie_authenticated = False
        self.csrf_token: Optional[str] = None
        self.api_token: Optional[str] = server.get('api_token') or None
        self.panel_version: Optional[str] = server.get('panel_version') or None
        self.api_profile: Optional[str] = server.get('panel_api_profile') or None
        self._profile_verified = False
        self.api_token_diagnostic: Optional[str] = None

        # Кеш настроек панели (subPort/subPath/subDomain/...) из setting/all.
        # Используется build_subscription_url() — за сессию запрашивается один раз.
        self._panel_settings: Optional[Dict[str, Any]] = None

        logger.debug(
            f"Инициализирован XUIClient для {server['name']}: {self.base_url} "
            f"(api_token={'есть' if self.api_token else 'нет'})"
        )
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Создаёт сессию если её нет."""
        if self.session is None or self.session.closed:
            # Unsafe=True важно для IP-адресов и самоподписанных сертификатов
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
        Сбрасывает текущую сессию.

        Вызывается при ошибках подключения для пересоздания сессии.
        CSRF-токен очищается — он привязан к серверной сессии.
        panel_mode и api_token НЕ сбрасываются — это политика, а не сессионное
        состояние. Их отдельно сбрасывает _invalidate_api_token() при ротации
        токена в панели.
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
        Сбрасывает Bearer-токен (при ротации в панели или 404 на Bearer-запросе).

        Очищает токен в БД (через update_server_api_token), чтобы при следующем
        запуске бот не пытался использовать невалидный токен.
        """
        if self.api_token is None:
            return
        self.api_token = None
        # panel_mode пересоздастся при следующем login() — может оказаться 'csrf'
        # (если токен протух, но панель всё ещё v3.0+) либо 'bearer' снова (если
        # фоновый login успеет вытянуть новый токен).
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
        """Возвращает dict/list из JSON-строки или уже распакованного значения."""
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
        """Нормализует JSON-поле inbound к строке для старой логики бота."""
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
        """Приводит inbound v3.1.0 с nested JSON к legacy-форме со строками."""
        if not isinstance(inbound, dict):
            return inbound
        normalized = dict(inbound)
        for field in JSON_INBOUND_FIELDS:
            normalized[field] = cls._json_field_to_text(normalized.get(field), "{}")
        return normalized

    @staticmethod
    def _normalize_tg_id(value: Any) -> int:
        """3x-ui v3.1.0 хранит tgId как int64; пустые и мусорные значения = 0."""
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _client_identifier_from_entry(client: Dict[str, Any]) -> str:
        """Возвращает технический идентификатор клиента для старых update/delete."""
        if not isinstance(client, dict):
            return ""
        return client.get("id") or client.get("password") or client.get("auth") or ""

    @classmethod
    def _find_client_in_inbounds(
        cls,
        inbounds: List[Dict[str, Any]],
        inbound_id: Optional[int] = None,
        client_uuid: Optional[str] = None,
        email: Optional[str] = None,
    ) -> tuple:
        """Ищет клиента в уже загруженном списке inbound без дополнительного запроса к панели."""
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
        """Собирает единый результат add_client из фактической записи панели."""
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
        """Сохраняет Bearer-токен в объекте и БД."""
        self.api_token = token
        self.server["api_token"] = token
        if self.server_id is not None:
            try:
                from database.db_servers import update_server_api_token
                update_server_api_token(self.server_id, token)
            except Exception as e:
                logger.warning(f"Не удалось сохранить api_token в БД: {e}")

    def _save_panel_info(self) -> None:
        """Сохраняет определённые version/profile панели в объекте и БД."""
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
        Преобразует ClientRecord/get_inbounds client в model.Client payload v3.1.0.

        В ответе /clients/get поле id — числовой ID записи БД, а UUID клиента
        лежит в uuid. В payload update поле id должно быть именно UUID.
        """
        if not isinstance(record, dict):
            record = {}

        uuid_value = record.get("uuid")
        record_id = record.get("id")
        if not uuid_value and isinstance(record_id, str):
            uuid_value = record_id
        if not uuid_value and fallback_uuid:
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
        for field in ("password", "auth", "flow"):
            value = record.get(field)
            if value:
                payload[field] = value
        reverse = record.get("reverse")
        if reverse:
            payload["reverse"] = reverse
        return {k: v for k, v in payload.items() if v != ""}

    @staticmethod
    def _split_clients_api_record(record: Dict[str, Any]) -> tuple:
        """Возвращает (client, inboundIds) из ответа /panel/api/clients/get/:email."""
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
        """Возвращает tuple версии 3x-ui для безопасного сравнения."""
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

    def _setting_bases(self) -> List[str]:
        """
        Возвращает порядок namespace для setting routes.

        До 3x-ui v3.3.0 настройки жили в /panel/setting/*, начиная с v3.3.0
        они переехали в /panel/api/setting/*. Если версия неизвестна, сначала
        пробуем старый путь, чтобы не менять поведение существующих панелей.
        """
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
        """Raw-запрос без login/_request, чтобы probes не зацикливались."""
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
        """Определяет версию панели через server/status с fallback на updateInfo."""
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
        """Feature-probe: v3.1.0+ имеет /panel/api/clients/list/paged."""
        headers = self._build_headers("GET")
        status, data = await self._raw_json_request(
            "GET",
            "/panel/api/clients/list/paged",
            headers=headers,
        )
        if status == 200 and isinstance(data, dict) and data.get("success"):
            return API_PROFILE_CLIENTS
        return API_PROFILE_LEGACY

    async def _refresh_panel_metadata(self, force: bool = False) -> None:
        """Обновляет version/profile панели и пишет кеш в servers."""
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
        """Гарантирует, что выбран профиль API для операций с клиентами."""
        if not self.is_authenticated:
            await self.login()
        if self.api_profile in (API_PROFILE_LEGACY, API_PROFILE_CLIENTS) and self._profile_verified:
            return self.api_profile
        if self.api_profile not in (API_PROFILE_LEGACY, API_PROFILE_CLIENTS) or not self._profile_verified:
            await self._refresh_panel_metadata(force=True)
        return self.api_profile or API_PROFILE_LEGACY

    @staticmethod
    def _is_legacy_client_endpoint(endpoint: str) -> bool:
        """True для старых client endpoints, исчезнувших в 3x-ui v3.1.0+."""
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
        При 404 на старом client endpoint перепроверяет профиль API.

        Если панель уже v3.1.0+ и поддерживает clients_api, текущий запрос нельзя
        ретраить тем же URL: вызывающая операция должна заново выбрать endpoint.
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
        """Один раз повторяет операцию, если 404 показал апгрейд API профиля."""
        try:
            return await operation()
        except StaleAPIProfileError:
            return await operation()

    async def _get_clients_api_record(self, email: str, log_error: bool = False) -> Optional[Dict[str, Any]]:
        """Возвращает запись клиента v3.1.0 по email или None."""
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
        """Проверяет, успела ли панель создать клиента до сетевого сбоя."""
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
        """Ищет клиента в /inbounds/list и возвращает (inbound, client)."""
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
        Определяет версию панели через probe GET /csrf-token.

        - HTTP 200 + JSON.obj → v3.0+ (CSRF middleware активен).
        - HTTP 404 → v2.x (endpoint не существует).
        - Любая другая ошибка → считаем legacy (безопасный фолбэк).

        Запрос идёт напрямую через session, без _request, чтобы не зациклиться.

        Returns:
            Кортеж (mode, csrf_token): ('csrf', '<token>') или ('legacy', None).
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
                # 404 или прочее — считаем v2.x
                logger.debug(f"Probe /csrf-token вернул {resp.status}, считаем v2.x legacy режим")
                return ('legacy', None)
        except asyncio.TimeoutError as e:
            raise TransientPanelError("Таймаут при проверке версии панели") from e
        except aiohttp.ClientError as e:
            raise TransientPanelError(f"Ошибка подключения при проверке версии панели: {e}") from e

    async def _fetch_api_token(self) -> Optional[str]:
        """
        Тянет Bearer-токен с панели v3.0+.

        На v3.0.2+/v3.1.0 использует /panel/setting/apiTokens.
        На v3.3.0+ использует /panel/api/setting/apiTokens:
        - берёт enabled token с именем YadrenoVPN Bot;
        - если токена нет, создаёт его;
        - если токен найден disabled, не включает его обратно и остаётся CSRF.

        На v3.0.0 падает обратно на старый /panel/setting/getApiToken.

        Returns:
            Токен или None если получить не удалось.
        """
        if self.csrf_token is None:
            logger.debug("Невозможно вытянуть api_token: csrf_token не установлен")
            return None

        headers = self._build_headers("GET", force_cookie=True, include_csrf_for_get=True)

        # Новый API токенов появился после v3.0.0. В v3.3.0 он переехал
        # из /panel/setting/* в /panel/api/setting/*.
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

        # Старый endpoint v3.0.0.
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
        Лёгкий probe-запрос для проверки актуальности Bearer-токена.

        Делает GET /panel/api/server/status с Authorization: Bearer.
        - 200 → токен валиден, переходим в режим 'bearer'.
        - 404/401 → токен невалиден (ротировали в панели).
        - 0 → панель временно недоступна, токен не очищаем.
        - Прочее → считаем невалидным.

        Returns:
            True если токен работает.
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
        Собирает HTTP-заголовки в зависимости от panel_mode.

        - legacy: только базовые AJAX-заголовки.
        - csrf: добавляет X-CSRF-Token для unsafe-методов.
        - bearer: добавляет Authorization: Bearer (CSRF не нужен).
        - force_cookie: для старого /panel/setting/* Bearer не подходит, нужен cookie+CSRF.
          Новый /panel/api/setting/* работает как обычный API namespace.
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
        log_error: bool = True
    ) -> Dict[str, Any]:
        """
        Выполняет HTTP-запрос к API.
        
        Args:
            method: HTTP метод (GET, POST)
            endpoint: Относительный путь (начинается с /panel/... или /login)
            data: Данные для POST запроса
            retry: Повторять ли при ошибках
            
        Returns:
            Ответ API в виде словаря
            
        Raises:
            VPNAPIError: При ошибке запроса
        """
        # URL = https://ip:port/secret_path/panel/...
        url = f"{self.base_url}{endpoint}"

        attempts = RETRY_CONFIG["max_attempts"] if retry else 1
        delays = RETRY_CONFIG["delays"]
        is_cookie_setting_route = endpoint.startswith(f"{SETTING_BASE_LEGACY}/")

        for attempt in range(attempts):
            try:
                # Получаем актуальную сессию (важно, так как она может быть пересоздана в _reset_session)
                session = await self._ensure_session()

                # Если нужна авторизация и мы не авторизованы (и это не запрос логина)
                if not self.is_authenticated and endpoint != "/login":
                    await self.login()

                if is_cookie_setting_route and endpoint != "/login":
                    await self._ensure_cookie_auth()

                # Заголовки собираются ПОСЛЕ login() — там определяется panel_mode
                # и устанавливаются csrf_token/api_token, нужные для _build_headers.
                headers = self._build_headers(
                    method,
                    force_cookie=is_cookie_setting_route,
                    include_csrf_for_get=is_cookie_setting_route,
                )

                logger.debug(f"API запрос: {method} {url} (mode={self.panel_mode})")

                async with session.request(method, url, json=data, headers=headers) as response:
                    text = await response.text()

                    # Bearer протух (ротировали в панели) — обнуляем токен, перелогиниваемся
                    if response.status == 401 and self.panel_mode == 'bearer' and not is_cookie_setting_route:
                        logger.warning(
                            f"HTTP 401 в режиме bearer — токен невалиден, "
                            f"переключаемся на обычный логин"
                        )
                        await self._invalidate_api_token()
                        await self._reset_session()
                        if attempt < attempts - 1:
                            continue

                    # CSRF-токен устарел (рестарт панели и т.п.) — переподтянуть и повторить
                    if response.status == 403 and (self.panel_mode == 'csrf' or is_cookie_setting_route):
                        logger.info("HTTP 403 — переподтягиваем CSRF-токен")
                        mode, token = await self._detect_panel_version()
                        if mode == 'csrf':
                            self.csrf_token = token
                            if attempt < attempts - 1:
                                continue

                    # Обработка статусов
                    if response.status == 200:
                        try:
                            result = json.loads(text)
                            if result.get("success"):
                                return result
                            
                            # Бывает success=False но есть msg
                            if "msg" in result and not result["success"]:
                                msg = result["msg"].lower()
                                # Проверяем на признаки истечения сессии
                                if any(x in msg for x in ["login", "auth", "session", "token"]):
                                    logger.warning(f"Сессия возможно истекла (msg='{result['msg']}'), пересоздаём...")
                                    await self._reset_session()
                                    if attempt < attempts - 1:
                                        # Сессия будет пересоздана при следующем запросе
                                        continue
                                        
                                raise VPNAPIError(result["msg"])
                            return result
                        except json.JSONDecodeError:
                            # Иногда возвращает HTML при редиректе на логин
                            if "login" in text.lower():
                                logger.warning("Сессия истекла (редирект на логин), пересоздаём...")
                                await self._reset_session()
                                if attempt < attempts - 1:
                                    # Сессия будет пересоздана при следующем запросе
                                    continue
                            logger.error(f"Невалидный JSON: {text[:100]}")
                            raise VPNAPIError("Некорректный ответ сервера")
                    elif response.status == 404:
                         await self._raise_if_stale_legacy_profile(endpoint)
                         # Некоторые версии X-UI возвращают 404 если сессия истекла
                         # Пытаемся пересоздать сессию
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
                # Сбрасываем сессию при ошибках подключения, чтобы пересоздать её
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
        Обычный login через cookie. Для v3.0+ добавляет CSRF.

        fetch_token=True используется основным login(), чтобы после cookie-логина
        получить Bearer. Для старого /panel/setting/* fetch_token=False, чтобы не вызвать
        рекурсию при обслуживании setting routes.
        """
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
                    "username": self.server["login"],
                    "password": self.server["password"],
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
        """Гарантирует cookie-сессию для старого /panel/setting/* даже в Bearer-режиме."""
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
        Авторизация в панели 3X-UI с авто-определением auth/API профиля.

        Алгоритм:
        1. Если есть сохранённый api_token — пробуем Bearer-валидацию (без логина).
           На успехе ставим panel_mode='bearer' и проверяем version/profile.
        2. Probe GET /csrf-token → 200 значит v3.0+, 404 значит v2.x.
        3. На v3.0+: логинимся с X-CSRF-Token, затем тянем/создаём api_token.
        4. На v2.x: обычный POST /login без CSRF.

        Returns:
            True при успешной авторизации

        Raises:
            VPNAPIError: При ошибке авторизации
        """
        logger.info(f"Авторизация на {self.server['name']}...")

        if self.api_token:
            if await self._try_bearer_validate():
                self.panel_mode = 'bearer'
                self.auth_mode = 'bearer'
                self.is_authenticated = True
                self.cookie_authenticated = False
                await self._refresh_panel_metadata(force=True)
                logger.info(f"✅ Авторизация через Bearer-токен (v3.0+) на {self.server['name']}")
                return True
            await self._invalidate_api_token()

        await self._login_with_cookie(fetch_token=True)
        await self._refresh_panel_metadata(force=True)
        return True

    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        """
        Получает список подключений (Inbounds).

        Args:
            include_ignored: True — вернуть также inbound'ы с префиксом --! в remark.

        Returns:
            Список inbound-подключений
        """
        result = await self._request("GET", "/panel/api/inbounds/list")
        obj = result.get("obj", [])
        if not isinstance(obj, list):
            return []
        inbounds = [
            self._normalize_inbound(inbound)
            for inbound in obj
            if isinstance(inbound, dict)
        ]
        if include_ignored:
            return inbounds
        return filter_visible_inbounds(inbounds)
    
    async def get_server_status(self) -> Dict[str, Any]:
        """
        Получает статус сервера (CPU, память, uptime).
        
        Returns:
            Словарь со статусом сервера
        """
        try:
            result = await self._request("GET", "/panel/api/server/status")
            return result.get("obj", {})
        except VPNAPIError:
            # Некоторые версии 3X-UI не имеют этого endpoint
            return {}

    async def get_stats(self) -> Dict[str, Any]:
        """
        Получает статистику сервера.
        
        Returns:
            Словарь со статистикой:
            - total_clients: Общее количество клиентов
            - active_clients: Количество активных клиентов (enable=True)
            - total_traffic_bytes: Общий трафик (up + down)
            - cpu_percent: Загрузка CPU (если доступно)
            - online: True если сервер доступен
        """
        try:
            inbounds = await self.get_inbounds()
            
            total_clients = 0
            active_clients = 0
            total_traffic = 0
            
            for inbound in inbounds:
                # Парсим настройки клиентов
                settings = self._load_json_field(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                total_clients += len(clients)

                for client in clients:
                    if client.get("enable", True):
                        active_clients += 1
                
                # Трафик inbound
                total_traffic += inbound.get("up", 0)
                total_traffic += inbound.get("down", 0)
            
            # Пробуем получить статус сервера (CPU)
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
        Получает список нод, подключённых к мастер-панели 3X-UI.

        Старые панели и панели без Node API возвращают 404. Для мониторинга это
        не ошибка: у такой панели просто нет доступного списка нод.
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
        Получает количество пользователей онлайн.
        
        Returns:
            Количество пользователей онлайн
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
    ) -> Dict[str, Any]:
        """
        Добавляет клиента в inbound.

        Args:
            inbound_id: ID inbound-подключения
            email: Уникальный идентификатор клиента (используем user_{id})
            total_gb: Лимит трафика в ГБ (0 = без лимита)
            expire_days: Срок действия в днях (0 = бессрочно)
            limit_ip: Ограничение по IP (1 = 1 устройство)
            enable: Активен ли клиент
            tg_id: Telegram ID для уведомлений панели
            flow: Параметр flow (напр. 'xtls-rprx-vision' для VLESS Reality/TLS TCP)
            sub_id: Subscription ID. Если передан — используется как есть (для
                режима subscription, где один subId должен быть на всех клиентах
                с одним email). Если None — генерируется новый uuid.

        Returns:
            Словарь с данными созданного клиента

        Raises:
            ValueError: Если expire_days <= 0
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

        # Определяем протокол inbound для правильной структуры клиента
        protocol = ""
        method = ""
        inbounds = []
        try:
            inbounds = await self.get_inbounds()
            for ib in inbounds:
                if ib['id'] == inbound_id:
                    protocol = ib.get('protocol', '')
                    settings = self._load_json_field(ib.get('settings', '{}'))
                    method = settings.get('method', '')
                    break
        except Exception:
            pass

        client_uuid = str(uuid.uuid4())
        
        # Для Shadowsocks 2022 требуется base64 пароль определенной длины
        if protocol == 'shadowsocks':
            import base64
            import os
            if method.startswith('2022-'):
                if '128' in method:
                    client_uuid = base64.b64encode(os.urandom(16)).decode('utf-8')
                else:
                    client_uuid = base64.b64encode(os.urandom(32)).decode('utf-8')
            else:
                # Для обычного SS лучше тоже использовать base64 (надежнее, чем uuid с дефисами)
                client_uuid = base64.urlsafe_b64encode(os.urandom(16)).decode('utf-8').rstrip('=')

        # Время истечения (timestamp в мс)
        expire_time = int((time.time() + expire_days * 86400) * 1000) if expire_days > 0 else 0
        
        # Лимит трафика (байты)
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
        
        # Базовая структура клиента
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
        
        # Протокол-зависимые поля
        if protocol == 'trojan':
            # Trojan использует password вместо id
            client_entry["password"] = client_uuid
            client_entry["flow"] = flow
        elif protocol == 'shadowsocks':
            # Shadowsocks — клиенты наследуют password/method из inbound
            client_entry["password"] = client_uuid
            client_entry["method"] = ""
        else:
            # VLESS / VMess — используют id (UUID)
            client_entry["id"] = client_uuid
            client_entry["flow"] = flow
        
        # Структура для 3X-UI
        client_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [client_entry]
            })
        }

        if profile == API_PROFILE_CLIENTS:
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
                                return recovered
                            if attempt >= add_attempts - 1:
                                raise
                            await wait_before_next_attempt(attempt)
                if flow and (existing_client.get("flow") or "") != flow:
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
                                return recovered
                            if attempt >= add_attempts - 1:
                                raise
                            await wait_before_next_attempt(attempt)
                    existing_client["flow"] = flow
                return existing_result

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
                        return recovered
                    if attempt >= add_attempts - 1:
                        raise
                    await wait_before_next_attempt(attempt)
            created_record = await self._get_clients_api_record(email, log_error=False)
            if created_record:
                created_client, _ = self._split_clients_api_record(created_record)
                return self._build_add_client_result(
                    created_client,
                    inbound_id,
                    email,
                    client_uuid,
                    expire_time,
                    total_gb,
                    client_entry["subId"],
                )
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

        return {
            "uuid": client_uuid,
            "email": email,
            "inbound_id": inbound_id,
            "expire_time": expire_time,
            "total_gb": total_gb,
            "sub_id": client_entry["subId"],
        }
    
    async def get_inbound_flow(self, inbound_id: int) -> str:
        """
        Определяет нужное значение flow для inbound.
        Flow = 'xtls-rprx-vision' нужен только для VLESS + TCP + (Reality или TLS).
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
                    
                    # Flow нужен только для VLESS + TCP + (reality | tls)
                    if network == 'tcp' and security in ('reality', 'tls'):
                        return 'xtls-rprx-vision'
                    return ""
        except Exception as e:
            logger.warning(f"Error determining flow for inbound {inbound_id}: {e}")
        return ""
    
    @staticmethod
    def _traffic_int(value: Any) -> Optional[int]:
        """Нормализует числовые поля трафика из разных версий API."""
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
        """Приводит ответы /clients/traffic и /clients/get к старому формату get_client_stats()."""
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
        Получает статистику трафика и протокол конкретного клиента.
        
        Args:
            email: Email/идентификатор клиента
            
        Returns:
            Словарь со статистикой или None:
            - up: Трафик за всё время (up) байт
            - down: Трафик за всё время (down) байт
            - total: Лимит трафика (байт)
            - protocol: Протокол соединения (vless, vmess и т.д.)
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
    
    async def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
        return await self._run_with_stale_profile_retry(
            lambda: self._delete_client_impl(inbound_id, client_uuid)
        )

    async def _delete_client_impl(self, inbound_id: int, client_uuid: str) -> bool:
        """
        Удаляет клиента из inbound.

        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента

        Returns:
            True при успешном удалении
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            _, client = await self._find_panel_client(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                include_ignored=True,
            )
            email = client.get("email") if isinstance(client, dict) else None
            if not email:
                email = client_uuid
            record = await self._get_clients_api_record(email, log_error=False)
            encoded_email = urllib.parse.quote(email, safe="")
            if record:
                _, inbound_ids = self._split_clients_api_record(record)
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
        Удаляет ВСЕХ клиентов с указанным email во всех inbound сервера.

        Используется в режиме subscription при замене ключа, удалении ключа
        и при переключении на режим keys (зачистка дубликатов).

        Args:
            email: Email/идентификатор клиента

        Returns:
            Количество фактически удалённых клиентов
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
        Включает/отключает ВСЕХ клиентов с указанным email во всех inbound сервера.

        Используется при истечении трафика или срока действия в режиме subscription:
        панель сама не отключает клиента по нашему счётчику (только по своему totalGB),
        поэтому отключаем вручную.

        Args:
            email: Email клиента
            enable: True — включить, False — отключить

        Returns:
            Количество обновлённых клиентов
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
                # Сохраняем все поля клиента, меняем только enable
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
        Получает настройки панели через setting/all.

        В частности возвращает поля subscription server: subEnable, subListen,
        subPort, subPath, subDomain, subURI, subCertFile, subKeyFile, subEncrypt.

        Результат кешируется на уровне клиента; повторные вызовы не делают запросов.

        Args:
            force_refresh: True — игнорировать кеш и сделать новый запрос.

        Returns:
            Словарь настроек или None, если не удалось получить.
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
        Возвращает HTTP-URL подписки для пользователя, собранный по настройкам
        subscription-server из API панели (subDomain/subPort/subPath/subURI).

        НЕ угадывает порт/путь по host:port API — берёт реальные значения с панели.
        Если у панели subEnable=false или настройки получить не удалось — возвращает
        None: пусть вызывающий код покажет пользователю осмысленную ошибку, а не
        выдаст битый URL.

        Args:
            sub_id: Subscription ID клиента

        Returns:
            Полный URL вида 'https://host:2096/sub/{sub_id}' или None.
        """
        settings = await self.get_panel_settings()
        if not settings:
            logger.warning(
                f"build_subscription_url: не удалось получить настройки панели "
                f"{self.server.get('name', self.server_id)}; URL не строится."
            )
            return None

        # Подписка вообще включена?
        if not settings.get("subEnable"):
            logger.warning(
                f"build_subscription_url: на панели {self.server.get('name', self.server_id)} "
                f"subscription отключена (subEnable=false). Включите её в настройках 3X-UI."
            )
            return None

        # Если админ задал кастомный subURI — это готовый префикс, добавляем только sub_id.
        sub_uri = (settings.get("subURI") or "").strip()
        if sub_uri:
            if not sub_uri.endswith("/"):
                sub_uri = sub_uri + "/"
            return f"{sub_uri}{sub_id}"

        # Собираем URL из компонент.
        from urllib.parse import urlparse
        sub_domain = (settings.get("subDomain") or "").strip()
        if not sub_domain:
            # Берём хост панели (без http://)
            parsed = urlparse(self.base_url)
            sub_domain = parsed.hostname or self.host

        sub_port = settings.get("subPort") or 0
        try:
            sub_port = int(sub_port)
        except (TypeError, ValueError):
            sub_port = 0

        # Путь: 3X-UI кладёт его как '/sub/' или 'sub/' — нормализуем.
        sub_path = settings.get("subPath") or "/"
        if not sub_path.startswith("/"):
            sub_path = "/" + sub_path
        if not sub_path.endswith("/"):
            sub_path = sub_path + "/"

        # Схема: HTTPS если у sub-server задан сертификат, иначе HTTP.
        # subKeyFile + subCertFile вместе означают TLS на sub-port.
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
        Обновляет лимит трафика существующего клиента.
        
        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента
            email: Email/идентификатор клиента
            total_gb: Новый лимит трафика в ГБ (0 = без лимита)
            
        Returns:
            True при успешном обновлении
        """
        profile = await self._ensure_api_profile()
        if profile == API_PROFILE_CLIENTS:
            return await self.update_client_limit(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                total_gb_bytes=total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0,
            )

        # Получаем текущие данные клиента
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
        
        # Обновляем лимит трафика
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
        target_client['totalGB'] = total_bytes
        
        # Формируем данные для обновления
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
        Отключает автопродление (сброс трафика/дней) при наступлении 1-го числа месяца для всех клиентов.
        Устанавливает поле reset = 0 для всех клиентов во всех inbounds.
        
        Returns:
            Количество обновленных клиентов.
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
                if client.get('reset', 0) != 0:  # только если reset не 0
                    
                    # clientId — это id(uuid) для vless/vmess, password для trojan/shadowsocks
                    client_id = client.get('id') or client.get('password')
                    
                    if client_id:
                        # Формируем правильную структуру клиента для обновления, сохраняя нужные поля
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
                            "reset": 0  # Сбрасываем reset
                        }
                        
                        # Удаляем пустые поля (важно для разных протоколов)
                        updated_client = {k: v for k, v in updated_client.items() if v != ''}
                        
                        client_data = {
                            "id": inbound['id'],
                            "settings": json.dumps({"clients": [updated_client]})
                        }
                        
                        try:
                            # В 3x-ui мы отправляем POST /panel/api/inbounds/updateClient/:clientId
                            # А в теле запроса передаем id инбаунда и новый объект clients
                            # Кодируем ID/пароль для URL, чтобы слеши в base64 (Shadowsocks) не ломали HTTP-маршрутизацию
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
    ) -> bool:
        """
        Обновляет ВСЕ параметры клиента на панели данными из нашей БД.
        Единственная функция записи на панель (кроме создания/удаления).
        
        Протокольные поля (flow, tgId) читаются с панели,
        но expiryTime, totalGB, enable, subId и limitIp при явной передаче берутся
        из параметров (из нашей БД).
        
        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента
            email: Email/идентификатор клиента
            expiry_time_ms: Срок действия в миллисекундах (из нашей БД, 0 = бессрочный)
            total_gb_bytes: Лимит трафика в байтах (из нашей БД, 0 = безлимит)
            enable: Явный статус клиента. None = сохранить текущее значение панели
            sub_id: Явный subscription ID. None = сохранить текущее значение панели
            limit_ip: Явный лимит устройств. None = сохранить текущее значение панели
            flow: Явный flow. None = сохранить текущее значение панели
            
        Returns:
            True при успешном обновлении
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

        # Читаем текущие данные клиента с панели — только для протокольных полей
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
        
        # Формируем данные: expiryTime и totalGB из ПАРАМЕТРОВ (нашей БД),
        # остальное — из текущих данных клиента на панели
        updated_client = {
            "id": target_client.get('id', ''),
            "password": target_client.get('password', ''),
            "flow": target_client.get('flow', '') if flow is None else flow,
            "email": target_client.get('email', email),
            "limitIp": target_client.get('limitIp', 1) if limit_ip is None else limit_ip,
            "totalGB": total_gb_bytes,          # ← Из нашей БД!
            "expiryTime": expiry_time_ms,        # ← Из нашей БД!
            "enable": target_client.get('enable', True) if enable is None else enable,
            "tgId": target_client.get('tgId', ''),
            "subId": target_client.get('subId', '') if sub_id is None else sub_id,
            "reset": 0  # Не используем auto-reset панели
        }
        
        # Удаляем пустые строковые поля (для разных протоколов)
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
        Продлевает срок действия клиента на указанное количество дней.
        Если срок уже истек, прибавляет дни к текущему времени.
        
        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента
            email: Email/идентификатор клиента
            days: Количество дней для продления
            
        Returns:
            True при успешном обновлении
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
        
        # Получаем текущие данные клиента
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
        
        # Расчет нового времени истечения
        extension_ms = days * 86400 * 1000
        if current_expiry == 0:
            # Бесконечный ключ остается бесконечным
            new_expiry = 0
        elif current_expiry < current_time_ms:
            # Если ключ уже истек, прибавляем к текущему моменту
            new_expiry = current_time_ms + extension_ms
        else:
            # Если еще активен, прибавляем к текущему сроку окончания
            new_expiry = current_expiry + extension_ms
            
        target_client['expiryTime'] = new_expiry
        
        # Формируем данные для обновления
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
        
        # Удаляем пустые поля (важно для разных протоколов, где id или password могут отсутствовать)
        clients_array = json.loads(update_data["settings"])["clients"][0]
        clients_array = {k: v for k, v in clients_array.items() if v != ''}
        update_data["settings"] = json.dumps({"clients": [clients_array]})
        
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        logger.info(f"Продлен ключ клиента {email} на {days} дней. Новый expiry: {new_expiry}")
        return True

    async def get_client_config(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Получает полную конфигурацию клиента для подключения.
        
        Args:
            email: Email/идентификатор клиента
            
        Returns:
            Словарь с настройками подключения или None
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
                    # Нашли клиента, возвращаем конфигурацию
                    stream_settings = self._load_json_field(inbound.get("streamSettings", "{}"))
                    protocol = inbound.get("protocol", "vless")
                    
                    # DEBUG: логируем stream_settings для отладки Reality-параметров
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
                    
                    # Протокол-специфичные поля
                    if protocol == 'trojan':
                        result["password"] = target_client.get("password", target_client.get("id", ""))
                    elif protocol == 'shadowsocks':
                        # Для Shadowsocks method хранится в inbound settings, 
                        # а пароль у каждого клиента свой (с fallback на общие)
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
        Получает VLESS-ссылку через endpoint подписки.
        
        Args:
            sub_id: Subscription ID клиента
            
        Returns:
            Готовая VLESS-ссылка или None если не удалось получить
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
        
        # Строим список URL кандидатов
        # 1. С base_path
        # 2. Без base_path
        # 3. /subscribe/ вместо /sub/ (иногда бывает)
        
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
                # Важно: Не используем _request, так как это публичный endpoint
                async with session.get(url, ssl=False) as response:
                    logger.info(f"Sub URL probe: {url} -> {response.status}")

                    if response.status == 200:
                        text = await response.text()
                        text = text.strip()

                        # Если вернул VLESS
                        if text.startswith("vless://") or text.startswith("vmess://") or text.startswith("trojan://"):
                            return text

                        # Если вернул base64
                        try:
                            import base64
                            # Добавляем паддинг если нужно
                            missing_padding = len(text) % 4
                            if missing_padding:
                                text += '=' * (4 - missing_padding)
                            decoded = base64.b64decode(text).decode('utf-8').strip()
                            if decoded.startswith("vless://") or decoded.startswith("vmess://") or decoded.startswith("trojan://"):
                                return decoded
                        except:
                            # Логируем, если это что-то странное
                            if len(text) < 200:
                                logger.debug(f"Unknown response text: {text}")
                            pass
            except Exception as e:
                logger.warning(f"Ошибка получения подписки ({url}): {e}")

        return None

    @staticmethod
    def _detect_database_backup(data: bytes) -> Optional[PanelDatabaseBackup]:
        """Определяет формат backup-файла 3X-UI по сигнатуре."""
        if data.startswith(b'SQLite format 3\x00'):
            return PanelDatabaseBackup(data=data, extension=".db", db_kind="sqlite")
        if data.startswith(b'PGDMP'):
            return PanelDatabaseBackup(data=data, extension=".dump", db_kind="postgres")
        return None

    async def get_database_backup(self) -> PanelDatabaseBackup:
        """
        Скачивает резервную копию базы данных панели.
        
        Endpoint: GET /panel/api/server/getDb (или фолбэки)
        
        Returns:
            PanelDatabaseBackup с байтами файла, расширением и типом БД
            
        Raises:
            VPNAPIError: При ошибке скачивания
        """
        session = await self._ensure_session()

        # Авторизуемся если нужно
        if not self.is_authenticated:
            await self.login()

        headers = {
            "Accept": "application/octet-stream",
            "X-Requested-With": "XMLHttpRequest"
        }
        # На v3.0+ через Bearer — обходим необходимость в cookie + CSRF
        if self.panel_mode == 'bearer' and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        # Разные версии X-UI / 3X-UI используют разные пути для скачивания БД
        endpoints = [
            "/panel/api/server/getDb",
            "/panel/setting/getDb",
            "/panel/api/getDb",
            "/server/getDb"
        ]
        
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
        Сбрасывает счётчики трафика (up/down) клиента на панели.
        
        Endpoint: POST /panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}
        
        Args:
            inbound_id: ID inbound-подключения
            email: Email/идентификатор клиента
            
        Returns:
            True при успешном сбросе
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
        Обновляет лимит трафика (totalGB) клиента на панели.
        
        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента
            email: Email/идентификатор клиента
            total_gb_bytes: Новый лимит в байтах
            
        Returns:
            True при успешном обновлении
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

        # Получаем текущие данные клиента
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
        
        # Обновляем totalGB
        target_client['totalGB'] = total_gb_bytes
        
        # Формируем данные для обновления
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
        
        # Удаляем пустые строковые поля (важно для разных протоколов)
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
        """Закрывает сессию."""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.is_authenticated = False
        self.cookie_authenticated = False
        self.csrf_token = None


# ============================================================================
# Глобальный кэш клиентов и вспомогательные функции
# ============================================================================
