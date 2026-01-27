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


class VPNAPIError(Exception):
    """Ошибка при работе с VPN API."""
    pass


class XUIClient:
    """
    Клиент для работы с API 3X-UI панели.
    
    Использует сессионную аутентификацию (cookie-based).
    ВАЖНО: Для 3X-UI куки могут быть привязаны к IP, поэтому используем unsafe=True для CookieJar.
    """
    
    def __init__(self, server: Dict[str, Any]):
        """
        Инициализация клиента.
        
        Args:
            server: Словарь с данными сервера (name, host, port, web_base_path, login, password)
        """
        self.server = server
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_authenticated = False
        
        # Формируем базовый URL
        # Base: https://ip:port/secret_path
        # Убираем конечный слеш из base_path если есть, чтобы не дублировать
        base_path = server['web_base_path'].rstrip('/')
        # Добавляем начальный слеш если нет
        if not base_path.startswith('/'):
            base_path = '/' + base_path
        self.base_url = f"https://{server['host']}:{server['port']}{base_path}"
        
        logger.debug(f"Инициализирован XUIClient для {server['name']}: {self.base_url}")
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Создаёт сессию если её нет."""
        if self.session is None or self.session.closed:
            # Unsafe=True важно для IP-адресов и самоподписанных сертификатов
            connector = aiohttp.TCPConnector(ssl=False)
            jar = aiohttp.CookieJar(unsafe=True)
            self.session = aiohttp.ClientSession(connector=connector, cookie_jar=jar)
            self.is_authenticated = False
            logger.debug(f"Создана новая сессия для {self.server['name']}")
        return self.session
    
    async def _reset_session(self) -> None:
        """
        Сбрасывает текущую сессию.
        
        Вызывается при ошибках подключения для пересоздания сессии.
        """
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug(f"Ошибка при закрытии сессии: {e}")
        self.session = None
        self.is_authenticated = False
        logger.debug(f"Сессия сброшена для {self.server['name']}")
    
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
        
        # Стандартные заголовки для AJAX запросов 3X-UI
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        attempts = RETRY_CONFIG["max_attempts"] if retry else 1
        delays = RETRY_CONFIG["delays"]
        
        for attempt in range(attempts):
            try:
                # Получаем актуальную сессию (важно, так как она может быть пересоздана в _reset_session)
                session = await self._ensure_session()

                # Если нужна авторизация и мы не авторизованы (и это не запрос логина)
                if not self.is_authenticated and endpoint != "/login":
                    await self.login()
                
                logger.debug(f"API запрос: {method} {url}")
                
                async with session.request(method, url, json=data, headers=headers) as response:
                    text = await response.text()
                    
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
        Авторизация в панели 3X-UI.
        
        Returns:
            True при успешной авторизации
            
        Raises:
            VPNAPIError: При ошибке авторизации
        """
        logger.info(f"Авторизация на {self.server['name']}...")
        
        session = await self._ensure_session()
        url = f"{self.base_url}/login"
        
        try:
            async with session.post(url, json={
                "username": self.server["login"],
                "password": self.server["password"]
            }) as resp:
                text = await resp.text()
                if resp.status == 200:
                    data = json.loads(text)
                    if data.get("success"):
                        self.is_authenticated = True
                        logger.info("✅ Успешная авторизация")
                        return True
                    else:
                        raise VPNAPIError(f"Ошибка логина: {data.get('msg')}")
                else:
                    raise VPNAPIError(f"HTTP {resp.status} при логине")
        except aiohttp.ClientError as e:
            raise VPNAPIError(f"Ошибка подключения при логине: {e}")
        except json.JSONDecodeError:
            raise VPNAPIError("Некорректный ответ при логине")

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
        tg_id: str = ""
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
            
        Returns:
            Словарь с данными созданного клиента
            
        Raises:
            ValueError: Если expire_days <= 0
        """
        if expire_days <= 0:
            raise ValueError("Срок действия ключа должен быть больше 0 дней")

        client_uuid = str(uuid.uuid4())
        
        # Время истечения (timestamp в мс)
        expire_time = int((time.time() + expire_days * 86400) * 1000) if expire_days > 0 else 0
        
        # Лимит трафика (байты)
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0
        
        # Структура для 3X-UI
        client_data = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": client_uuid,
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_bytes,
                    "expiryTime": expire_time,
                    "enable": enable,
                    "tgId": tg_id,
                    "tgId": tg_id,
                    "subId": uuid.uuid4().hex,
                    "reset": 30  # Сброс счётчика трафика каждые 30 дней
                }]
            })
        }
        
        await self._request("POST", "/panel/api/inbounds/addClient", data=client_data)
        
        return {
            "uuid": client_uuid,
            "email": email,
            "inbound_id": inbound_id,
            "expire_time": expire_time,
            "total_gb": total_gb
        }
    
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
        await self._request("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}")
        return True
    
    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        """
        Сбрасывает счётчик трафика клиента.
        
        Args:
            inbound_id: ID inbound-подключения
            email: Email/идентификатор клиента
            
        Returns:
            True при успешном сбросе
        """
        await self._request("POST", f"/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}")
        logger.info(f"Сброшен трафик клиента {email} в inbound {inbound_id}")
        return True
    
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
                    "reset": target_client.get('reset', 30)
                }]
            })
        }
        
        await self._request("POST", f"/panel/api/inbounds/updateClient/{client_uuid}", data=update_data)
        logger.info(f"Обновлен лимит трафика клиента {email}: {total_gb} ГБ")
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
                    
                    # DEBUG: логируем stream_settings для отладки Reality-параметров
                    logger.debug(f"Stream settings for {email}: {json.dumps(stream_settings, ensure_ascii=False)}")
                    if stream_settings.get("security") == "reality":
                        reality = stream_settings.get("realitySettings", {})
                        logger.info(f"Reality settings for {email}: pbk={reality.get('publicKey')}, sni={reality.get('serverName')}, fp={reality.get('fingerprint')}, shortIds={reality.get('shortIds')}")
                    
                    return {
                        "uuid": target_client["id"],
                        "email": target_client["email"],
                        "port": inbound["port"],
                        "protocol": inbound["protocol"],
                        "host": self.server["host"], # Внешний адрес
                        "stream_settings": stream_settings,
                        "inbound_name": inbound.get("remark", "VPN"),
                        "sub_id": target_client.get("subId", ""),
                        "flow": target_client.get("flow", "")
                    }
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
        
        Endpoint: GET /panel/api/getDb
        
        Returns:
            Бинарные данные файла x-ui.db
            
        Raises:
            VPNAPIError: При ошибке скачивания
        """
        session = await self._ensure_session()
        
        # Авторизуемся если нужно
        if not self.is_authenticated:
            await self.login()
        
        url = f"{self.base_url}/panel/api/getDb"
        
        headers = {
            "Accept": "application/octet-stream",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.read()
                    logger.info(f"Скачан бэкап БД панели: {len(data)} байт")
                    return data
                else:
                    text = await response.text()
                    raise VPNAPIError(f"Ошибка скачивания бэкапа: HTTP {response.status}")
        except aiohttp.ClientError as e:
            raise VPNAPIError(f"Ошибка подключения при скачивании бэкапа: {e}")

    async def close(self):
        """Закрывает сессию."""
        if self.session:
            await self.session.close()
            self.session = None


# ============================================================================
# Глобальный кэш клиентов и вспомогательные функции
# ============================================================================

_clients: Dict[int, XUIClient] = {}


def get_client_from_server_data(server: Dict[str, Any]) -> XUIClient:
    """
    Создаёт клиент из данных сервера.
    
    Args:
        server: Словарь с данными сервера из БД
        
    Returns:
        Экземпляр XUIClient
    """
    server_id = server['id']
    
    # Проверяем кэш
    if server_id in _clients:
        return _clients[server_id]
    
    # Создаём нового клиента
    client = XUIClient(server)
    _clients[server_id] = client
    return client


async def get_client(server_id: int) -> XUIClient:
    """
    Получает клиент для сервера по ID (из БД).
    
    Args:
        server_id: ID сервера в БД
        
    Returns:
        Экземпляр XUIClient
        
    Raises:
        ValueError: Если сервер не найден
    """
    # Импортируем здесь чтобы избежать циклических импортов
    from database.requests import get_server_by_id
    
    # Проверяем кэш
    if server_id in _clients:
        return _clients[server_id]
    
    # Получаем данные сервера из БД
    server = get_server_by_id(server_id)
    if not server:
        raise ValueError(f"Сервер с ID {server_id} не найден")
    
    return get_client_from_server_data(server)


async def test_server_connection(server_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет подключение к серверу.
    
    Args:
        server_data: Словарь с данными сервера
        
    Returns:
        Словарь с результатом:
        - success: True если подключение успешно
        - message: Сообщение о результате
        - stats: Статистика (если успешно)
    """
    # Создаём временный клиент
    client = XUIClient(server_data)
    
    try:
        # Пробуем авторизоваться
        await client.login()
        
        # Получаем статистику
        stats = await client.get_stats()
        
        return {
            "success": True,
            "message": "Подключение успешно!",
            "stats": stats
        }
    except VPNAPIError as e:
        return {
            "success": False,
            "message": f"Ошибка: {e}",
            "stats": None
        }
    finally:
        await client.close()


def invalidate_client_cache(server_id: int):
    """
    Удаляет клиент из кэша (при изменении настроек сервера).
    
    Args:
        server_id: ID сервера
    """
    if server_id in _clients:
        client = _clients[server_id]
        # Запускаем закрытие сессии, если она есть
        if client.session and not client.session.closed:
            try:
                # Создаём задачу на закрытие в фоне
                import asyncio
                asyncio.create_task(client.close())
            except RuntimeError:
                # Если нет активного event loop, просто пропускаем
                pass
        del _clients[server_id]
        logger.debug(f"Клиент сервера {server_id} удалён из кэша")


async def close_all_clients():
    """Закрывает все сессии."""
    for client in _clients.values():
        await client.close()
    _clients.clear()


# ============================================================================
# Форматирование трафика
# ============================================================================

def format_traffic(bytes_count: int) -> str:
    """
    Форматирует трафик в читаемый вид.
    
    Args:
        bytes_count: Количество байт
        
    Returns:
        Отформатированная строка (например, "1.2 TB")
    """
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 ** 2:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 ** 3:
        return f"{bytes_count / 1024 ** 2:.1f} MB"
    elif bytes_count < 1024 ** 4:
        return f"{bytes_count / 1024 ** 3:.2f} GB"
    else:
        return f"{bytes_count / 1024 ** 4:.2f} TB"
