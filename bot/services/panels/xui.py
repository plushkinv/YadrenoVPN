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
import uuid
import time
from typing import Optional, Dict, Any, List
from config import RETRY_CONFIG

logger = logging.getLogger(__name__)


from .base import BaseVPNClient, VPNAPIError
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

        # Поддержка 3x-ui v3.0+: panel_mode определяется при первом логине.
        # 'legacy' = v2.x cookie; 'csrf' = v3.0+ cookie + X-CSRF-Token;
        # 'bearer' = v3.0+ через Authorization: Bearer (минует CSRF).
        # None = ещё не определялся (нужен probe /csrf-token).
        self.panel_mode: Optional[str] = None
        self.csrf_token: Optional[str] = None
        self.api_token: Optional[str] = server.get('api_token') or None

        # Кеш настроек панели (subPort/subPath/subDomain/...) из /panel/setting/all.
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
            timeout = aiohttp.ClientTimeout(total=5)
            self.session = aiohttp.ClientSession(connector=connector, cookie_jar=jar, timeout=timeout)
            self.is_authenticated = False
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
        if self.server_id is not None:
            try:
                from database.db_servers import update_server_api_token
                update_server_api_token(self.server_id, None)
            except Exception as e:
                logger.warning(f"Не удалось очистить api_token в БД для server_id={self.server_id}: {e}")
    
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
        except aiohttp.ClientError as e:
            logger.debug(f"Probe /csrf-token упал ({e}), считаем v2.x legacy режим")
            return ('legacy', None)

    async def _fetch_api_token(self) -> Optional[str]:
        """
        Тянет Bearer-токен с панели v3.0+ через GET /panel/setting/getApiToken.

        Использует уже установленную сессию (cookie) и CSRF-токен.
        На успехе атомарно сохраняет токен в БД.

        Returns:
            Токен или None если получить не удалось.
        """
        if self.csrf_token is None:
            logger.debug("Невозможно вытянуть api_token: csrf_token не установлен")
            return None
        session = await self._ensure_session()
        url = f"{self.base_url}/panel/setting/getApiToken"
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.debug(f"GET /panel/setting/getApiToken вернул {resp.status}")
                    return None
                try:
                    data = await resp.json(content_type=None)
                except json.JSONDecodeError:
                    return None
                if not isinstance(data, dict) or not data.get('success'):
                    return None
                token = data.get('obj')
                if not isinstance(token, str) or not token:
                    return None
                # Сохраняем в БД
                if self.server_id is not None:
                    try:
                        from database.db_servers import update_server_api_token
                        update_server_api_token(self.server_id, token)
                    except Exception as e:
                        logger.warning(f"Не удалось сохранить api_token в БД: {e}")
                return token
        except aiohttp.ClientError as e:
            logger.debug(f"Ошибка при вытягивании api_token: {e}")
            return None

    async def _try_bearer_validate(self) -> bool:
        """
        Лёгкий probe-запрос для проверки актуальности Bearer-токена.

        Делает GET /panel/api/inbounds/list с Authorization: Bearer.
        - 200 → токен валиден, переходим в режим 'bearer'.
        - 404/401 → токен невалиден (ротировали в панели).
        - Прочее → считаем невалидным.

        Returns:
            True если токен работает.
        """
        if not self.api_token:
            return False
        session = await self._ensure_session()
        url = f"{self.base_url}/panel/api/inbounds/list"
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Authorization": f"Bearer {self.api_token}",
        }
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return True
                logger.info(f"Bearer-токен невалиден (HTTP {resp.status}), нужно обновить")
                return False
        except aiohttp.ClientError as e:
            logger.debug(f"Ошибка при проверке Bearer-токена: {e}")
            return False

    def _build_headers(self, method: str) -> Dict[str, str]:
        """
        Собирает HTTP-заголовки в зависимости от panel_mode.

        - legacy: только базовые AJAX-заголовки.
        - csrf: добавляет X-CSRF-Token для unsafe-методов.
        - bearer: добавляет Authorization: Bearer (CSRF не нужен).
        """
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.panel_mode == 'bearer' and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        elif self.panel_mode == 'csrf' and self.csrf_token and method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
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

        for attempt in range(attempts):
            try:
                # Получаем актуальную сессию (важно, так как она может быть пересоздана в _reset_session)
                session = await self._ensure_session()

                # Если нужна авторизация и мы не авторизованы (и это не запрос логина)
                if not self.is_authenticated and endpoint != "/login":
                    await self.login()

                # Заголовки собираются ПОСЛЕ login() — там определяется panel_mode
                # и устанавливаются csrf_token/api_token, нужные для _build_headers.
                headers = self._build_headers(method)

                logger.debug(f"API запрос: {method} {url} (mode={self.panel_mode})")

                async with session.request(method, url, json=data, headers=headers) as response:
                    text = await response.text()

                    # Bearer протух (ротировали в панели) — обнуляем токен, перелогиниваемся
                    if response.status in (401, 404) and self.panel_mode == 'bearer':
                        logger.warning(
                            f"HTTP {response.status} в режиме bearer — токен невалиден, "
                            f"переключаемся на обычный логин"
                        )
                        await self._invalidate_api_token()
                        await self._reset_session()
                        if attempt < attempts - 1:
                            continue

                    # CSRF-токен устарел (рестарт панели и т.п.) — переподтянуть и повторить
                    if response.status == 403 and self.panel_mode == 'csrf':
                        logger.info("HTTP 403 в режиме csrf — переподтягиваем CSRF-токен")
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
                    
            except aiohttp.ClientError as e:
                logger.warning(f"Ошибка подключения (попытка {attempt+1}): {e}")
                # Сбрасываем сессию при ошибках подключения, чтобы пересоздать её
                await self._reset_session()
                if attempt < attempts - 1:
                    await asyncio.sleep(delays[attempt])
                else:
                    raise VPNAPIError(f"Ошибка подключения: {e}")
            except VPNAPIError:
                raise
            except Exception as e:
                logger.error(f"Неожиданная ошибка: {e}")
                raise VPNAPIError(f"Неожиданная ошибка: {e}")
        
        raise VPNAPIError("Превышено количество попыток")

    async def login(self) -> bool:
        """
        Авторизация в панели 3X-UI с авто-определением версии (v2.x vs v3.0+).

        Алгоритм:
        1. Если есть сохранённый api_token — пробуем Bearer-валидацию (без логина).
           На успехе ставим panel_mode='bearer' и выходим. На неудаче — обнуляем токен.
        2. Probe GET /csrf-token → 200 значит v3.0+, 404 значит v2.x.
        3. На v3.0+: логинимся с заголовком X-CSRF-Token, затем тянем api_token
           через GET /panel/setting/getApiToken и переходим в режим 'bearer'.
        4. На v2.x: обычный POST /login без CSRF.

        Returns:
            True при успешной авторизации

        Raises:
            VPNAPIError: При ошибке авторизации
        """
        logger.info(f"Авторизация на {self.server['name']}...")

        # === Шаг 1: пробуем Bearer, если токен уже сохранён ===
        if self.api_token:
            if await self._try_bearer_validate():
                self.panel_mode = 'bearer'
                self.is_authenticated = True
                logger.info(f"✅ Авторизация через Bearer-токен (v3.0+) на {self.server['name']}")
                return True
            # Bearer не сработал — токен протух, чистим и переходим к обычному логину
            await self._invalidate_api_token()

        # === Шаг 2: определяем версию через probe /csrf-token ===
        mode, csrf_token = await self._detect_panel_version()
        self.panel_mode = mode
        self.csrf_token = csrf_token  # None для legacy, строка для csrf

        # === Шаг 3: обычный POST /login ===
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
                        logger.info(f"✅ Успешная авторизация на {self.server['name']} (режим={mode})")
                    else:
                        raise VPNAPIError(f"Ошибка логина: {data.get('msg')}")
                elif resp.status == 404:
                    raise VPNAPIError(f"Панель недоступна по пути {self.server['web_base_path']}")
                elif resp.status == 403:
                    raise VPNAPIError("Ошибка CSRF при логине (HTTP 403). Возможно, панель v3.0+ требует X-CSRF-Token")
                else:
                    raise VPNAPIError(f"HTTP {resp.status} при логине")
        except aiohttp.ClientConnectorError:
            raise VPNAPIError(
                f"Не удалось подключиться к {self.server.get('protocol', 'https')}://"
                f"{self.server['host']}:{self.server['port']}"
            )
        except asyncio.TimeoutError:
            raise VPNAPIError("Таймаут при логине")
        except json.JSONDecodeError:
            raise VPNAPIError("Некорректный ответ при логине")

        # === Шаг 4: на v3.0+ автоматически вытягиваем api_token и переходим в bearer ===
        if mode == 'csrf':
            token = await self._fetch_api_token()
            if token:
                self.api_token = token
                self.panel_mode = 'bearer'
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

    async def get_inbounds(self) -> List[Dict[str, Any]]:
        """
        Получает список подключений (Inbounds).
        
        Returns:
            Список inbound-подключений
        """
        result = await self._request("GET", "/panel/api/inbounds/list")
        return result.get("obj", [])
    
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
                settings_str = inbound.get("settings", "{}")
                try:
                    settings = json.loads(settings_str)
                    clients = settings.get("clients", [])
                    total_clients += len(clients)
                    
                    for client in clients:
                        if client.get("enable", True):
                            active_clients += 1
                except json.JSONDecodeError:
                    pass
                
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
        """
        Получает количество пользователей онлайн.
        
        Returns:
            Количество пользователей онлайн
        """
        try:
            # Запрос к /panel/api/inbounds/onlines
            response = await self._request("POST", "/panel/api/inbounds/onlines", retry=False, log_error=False)
            if response.get("success") and response.get("obj"):
                return len(response["obj"])
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

        # Определяем протокол inbound для правильной структуры клиента
        protocol = ""
        method = ""
        try:
            inbounds = await self.get_inbounds()
            for ib in inbounds:
                if ib['id'] == inbound_id:
                    protocol = ib.get('protocol', '')
                    settings_raw = ib.get('settings', '{}')
                    if isinstance(settings_raw, str):
                        settings = json.loads(settings_raw)
                    else:
                        settings = settings_raw
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
        
        await self._request("POST", "/panel/api/inbounds/addClient", data=client_data)

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
    
    async def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
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
            inbounds = await self.get_inbounds()
            for inbound in inbounds:
                client_stats = inbound.get("clientStats", [])
                for stats in client_stats:
                    if stats.get("email") == email:
                        return {
                            "up": stats.get("up", 0),
                            "down": stats.get("down", 0),
                            "total": stats.get("total", 0),
                            "protocol": inbound.get("protocol", "vless"),
                            "remark": inbound.get("remark", ""),
                            "expiry_time": stats.get("expiryTime", 0)
                        }
        except Exception as e:
            logger.warning(f"Ошибка получения статистики клиента {email}: {e}")
        return None
    
    async def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
        """
        Удаляет клиента из inbound.

        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента

        Returns:
            True при успешном удалении
        """
        import urllib.parse
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{encoded_uuid}")
        return True

    async def delete_clients_by_email_on_server(self, email: str) -> int:
        """
        Удаляет ВСЕХ клиентов с указанным email во всех inbound сервера.

        Используется в режиме subscription при замене ключа, удалении ключа
        и при переключении на режим keys (зачистка дубликатов).

        Args:
            email: Email/идентификатор клиента

        Returns:
            Количество фактически удалённых клиентов
        """
        inbounds = await self.get_inbounds()
        deleted = 0
        for inbound in inbounds:
            try:
                settings_raw = inbound.get('settings', '{}')
                settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            except (json.JSONDecodeError, TypeError):
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
        import urllib.parse
        inbounds = await self.get_inbounds()
        count = 0
        for inbound in inbounds:
            try:
                settings_raw = inbound.get('settings', '{}')
                settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            except (json.JSONDecodeError, TypeError):
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
        Получает настройки панели через POST /panel/setting/all.

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
        try:
            resp = await self._request("POST", "/panel/setting/all")
        except Exception as e:
            logger.warning(f"get_panel_settings: запрос не удался: {e}")
            return None
        if not isinstance(resp, dict) or not resp.get("success"):
            logger.warning(f"get_panel_settings: панель ответила без success: {resp}")
            return None
        obj = resp.get("obj")
        if not isinstance(obj, dict):
            return None
        self._panel_settings = obj
        return obj

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
        # Получаем текущие данные клиента
        inbounds = await self.get_inbounds()
        target_inbound = None
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                target_inbound = inbound
                settings = json.loads(inbound.get('settings', '{}'))
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
        settings = json.loads(target_inbound.get('settings', '{}'))
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
        
        import urllib.parse
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        logger.info(f"Обновлен лимит трафика клиента {email}: {total_gb} ГБ")
        return True

    async def disable_reset_for_all_clients(self) -> int:
        """
        Отключает автопродление (сброс трафика/дней) при наступлении 1-го числа месяца для всех клиентов.
        Устанавливает поле reset = 0 для всех клиентов во всех inbounds.
        
        Returns:
            Количество обновленных клиентов.
        """
        updated_count = 0
        inbounds = await self.get_inbounds()
        
        for inbound in inbounds:
            settings_raw = inbound.get('settings', '{}')
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
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
                            import urllib.parse
                            # Кодируем ID/пароль для URL, чтобы слеши в base64 (Shadowsocks) не ломали HTTP-маршрутизацию
                            encoded_id = urllib.parse.quote(client_id, safe='')
                            await self._request(
                                "POST",
                                f"/panel/api/inbounds/updateClient/{encoded_id}",
                                data=client_data
                            )
                            updated_count += 1
                            logger.info(f"Отключено автопродление (reset=0) для клиента {client.get('email', client_id)}")
                        except Exception as e:
                            logger.error(f"Ошибка при отключении автопродления для клиента {client.get('email', client_id)}: {e}")
                            
        return updated_count

    async def update_client_full(
        self,
        inbound_id: int,
        client_uuid: str,
        email: str,
        expiry_time_ms: int,
        total_gb_bytes: int
    ) -> bool:
        """
        Обновляет ВСЕ параметры клиента на панели данными из нашей БД.
        Единственная функция записи на панель (кроме создания/удаления).
        
        Протокольные поля (flow, subId, limitIp, tgId) читаются с панели,
        но expiryTime и totalGB ВСЕГДА берутся из параметров (из нашей БД).
        
        Args:
            inbound_id: ID inbound-подключения
            client_uuid: UUID клиента
            email: Email/идентификатор клиента
            expiry_time_ms: Срок действия в миллисекундах (из нашей БД, 0 = бессрочный)
            total_gb_bytes: Лимит трафика в байтах (из нашей БД, 0 = безлимит)
            
        Returns:
            True при успешном обновлении
        """
        # Читаем текущие данные клиента с панели — только для протокольных полей
        inbounds = await self.get_inbounds()
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                settings = json.loads(inbound.get('settings', '{}'))
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
            "flow": target_client.get('flow', ''),
            "email": target_client.get('email', email),
            "limitIp": target_client.get('limitIp', 1),
            "totalGB": total_gb_bytes,          # ← Из нашей БД!
            "expiryTime": expiry_time_ms,        # ← Из нашей БД!
            "enable": target_client.get('enable', True),
            "tgId": target_client.get('tgId', ''),
            "subId": target_client.get('subId', ''),
            "reset": 0  # Не используем auto-reset панели
        }
        
        # Удаляем пустые строковые поля (для разных протоколов)
        updated_client = {k: v for k, v in updated_client.items() if v != ''}
        
        update_data = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [updated_client]})
        }
        
        import urllib.parse
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        
        from datetime import datetime
        expiry_str = datetime.fromtimestamp(expiry_time_ms / 1000).strftime('%Y-%m-%d %H:%M') if expiry_time_ms > 0 else '∞'
        limit_str = f"{total_gb_bytes / 1024**3:.1f} ГБ" if total_gb_bytes > 0 else '∞'
        logger.info(f"Обновлён клиент {email}: expiry={expiry_str}, limit={limit_str}")
        return True

    async def extend_client_expiry(
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
        
        # Получаем текущие данные клиента
        inbounds = await self.get_inbounds()
        target_inbound = None
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                target_inbound = inbound
                settings = json.loads(inbound.get('settings', '{}'))
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
        
        import urllib.parse
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
                settings = json.loads(inbound.get("settings", "{}"))
                clients = settings.get("clients", [])
                
                target_client = None
                for client in clients:
                    if client.get("email") == email:
                        target_client = client
                        break
                
                if target_client:
                    # Нашли клиента, возвращаем конфигурацию
                    stream_settings = json.loads(inbound.get("streamSettings", "{}"))
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

    async def get_database_backup(self) -> bytes:
        """
        Скачивает резервную копию базы данных панели.
        
        Endpoint: GET /panel/api/server/getDb (или фолбэки)
        
        Returns:
            Бинарные данные файла x-ui.db
            
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
        for endpoint in endpoints:
            url = f"{self.base_url}{endpoint}"
            try:
                async with session.get(url, headers=headers) as response:
                    last_status = response.status
                    if response.status == 200:
                        data = await response.read()
                        
                        # Проверяем, что скачался действительно SQLite файл
                        # SQLite файлы всегда начинаются с байтов 'SQLite format 3\000'
                        if data.startswith(b'SQLite format 3\x00'):
                            logger.info(f"Скачан бэкап БД панели ({endpoint}): {len(data)} байт")
                            return data
                        else:
                            text = data[:100].decode(errors='ignore')
                            logger.debug(f"Endpoint {endpoint} вернул не БД, а: {text}...")
            except aiohttp.ClientError as e:
                logger.debug(f"Ошибка HTTP при проверке {endpoint}: {e}")
                
        raise VPNAPIError(f"Ошибка скачивания бэкапа: ни один endpoint не вернул файл БД. Последний HTTP статус: {last_status}")

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        """
        Сбрасывает счётчики трафика (up/down) клиента на панели.
        
        Endpoint: POST /panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}
        
        Args:
            inbound_id: ID inbound-подключения
            email: Email/идентификатор клиента
            
        Returns:
            True при успешном сбросе
        """
        import urllib.parse
        encoded_email = urllib.parse.quote(email, safe='')
        result = await self._request(
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
        # Получаем текущие данные клиента
        inbounds = await self.get_inbounds()
        target_client = None
        
        for inbound in inbounds:
            if inbound.get('id') == inbound_id:
                settings = json.loads(inbound.get('settings', '{}'))
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
        
        import urllib.parse
        encoded_uuid = urllib.parse.quote(client_uuid, safe='')
        await self._request("POST", f"/panel/api/inbounds/updateClient/{encoded_uuid}", data=update_data)
        
        limit_gb = total_gb_bytes / (1024**3)
        logger.info(f"Обновлён лимит клиента {email}: {limit_gb:.1f} ГБ")
        return True

    async def close(self):
        """Закрывает сессию."""
        if self.session:
            await self.session.close()
            self.session = None


# ============================================================================
# Глобальный кэш клиентов и вспомогательные функции
# ============================================================================
