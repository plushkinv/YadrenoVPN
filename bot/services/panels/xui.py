"""Official 3X-UI 3.3+ Bearer/Clients API adapter."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Callable, Dict, Iterable, List, Optional

import aiohttp

from config import RETRY_CONFIG

from bot.utils.inbounds import (
    filter_visible_inbounds,
    inbound_matches_group,
    is_mtproto_inbound,
)
from bot.utils.panel_version import (
    CLIENT_EXTERNAL_LINKS_MIN_VERSION,
    CLIENT_HWID_LIMITS_MIN_VERSION,
    MINIMUM_SUPPORTED_3X_UI_VERSION,
    panel_version_at_least,
    parse_panel_version,
)

from .base import (
    BaseVPNClient,
    PanelClientDevice,
    PanelClientState,
    PanelDatabaseBackup,
    PanelErrorKind,
    PanelInboundDescriptor,
    PanelProvisionResult,
    PanelRejectedError,
    PanelRequestError,
    PanelServerSnapshot,
    VPNAPIError,
    build_inbound_descriptor,
)


logger = logging.getLogger(__name__)

DEFAULT_PANEL_TIMEOUT_SECONDS = 15
CAPABILITY_REFRESH_INTERVAL_SECONDS = 300
BOT_API_TOKEN_NAME = "YadrenoVPN Bot"
MTPROTO_MULTI_CLIENT_MIN_VERSION = (3, 5, 0)
JSON_INBOUND_FIELDS = ("settings", "streamSettings", "sniffing")
_PANEL_AUTH_LOCKS: Dict[str, asyncio.Lock] = {}


class XUIClient(BaseVPNClient):
    """3X-UI client whose ordinary operations always use Bearer auth."""

    def __init__(self, server: dict):
        self.server = server
        self.server_id = server.get("id")
        self.host = str(server["host"])
        self.port = int(server["port"])
        self.protocol = str(server.get("protocol") or "https")
        path = str(server.get("web_base_path") or "").strip("/")
        path = f"/{path}" if path else ""
        self.base_url = f"{self.protocol}://{self.host}:{self.port}{path}"
        self._panel_auth_key = (
            f"{self.protocol.casefold()}://{self.host.casefold()}:{self.port}{path}"
        )

        self.session: Optional[aiohttp.ClientSession] = None
        self.api_token: Optional[str] = str(server.get("api_token") or "") or None
        self.panel_version: Optional[str] = str(server.get("panel_version") or "") or None
        raw_inbound_group_id = server.get("inbound_group_id")
        if raw_inbound_group_id in (None, ""):
            self.inbound_group_id: Optional[int] = None
        else:
            self.inbound_group_id = int(raw_inbound_group_id)
            if self.inbound_group_id <= 0:
                raise ValueError("inbound_group_id must be a positive integer")
        self.is_authenticated = False
        self._validated_token: Optional[str] = None
        self._session_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._capability_refresh_lock = asyncio.Lock()
        self._capability_checked_monotonic = 0.0
        self._panel_settings: Optional[Dict[str, Any]] = None
        self._operation_metrics: contextvars.ContextVar[Optional[Dict[str, Any]]] = (
            contextvars.ContextVar(f"xui_operation_metrics_{id(self)}", default=None)
        )

    # ------------------------------------------------------------------
    # Session, metrics, typed request failures
    # ------------------------------------------------------------------

    def _has_password_credentials(self) -> bool:
        return bool(
            str(self.server.get("login") or "").strip()
            and str(self.server.get("password") or "").strip()
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is not None and not self.session.closed:
            return self.session
        async with self._session_lock:
            if self.session is not None and not self.session.closed:
                return self.session
            try:
                timeout_seconds = float(
                    RETRY_CONFIG.get("timeout_seconds", DEFAULT_PANEL_TIMEOUT_SECONDS)
                )
            except (TypeError, ValueError):
                timeout_seconds = DEFAULT_PANEL_TIMEOUT_SECONDS
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False, limit_per_host=8),
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            )
        return self.session

    @asynccontextmanager
    async def operation_metrics(self, operation: str):
        metrics = {
            "operation": str(operation),
            "requests": 0,
            "response_bytes": 0,
            "retries": 0,
            "started": time.monotonic(),
        }
        context_token = self._operation_metrics.set(metrics)
        try:
            yield metrics
        finally:
            elapsed_ms = int((time.monotonic() - metrics["started"]) * 1000)
            log = logger.warning if elapsed_ms >= 5000 else logger.info
            log(
                "panel_operation server_id=%s operation=%s requests=%s "
                "response_bytes=%s retries=%s elapsed_ms=%s",
                self.server_id,
                metrics["operation"],
                metrics["requests"],
                metrics["response_bytes"],
                metrics["retries"],
                elapsed_ms,
            )
            self._operation_metrics.reset(context_token)

    def _record_attempt(self, *, retry: bool = False) -> None:
        metrics = self._operation_metrics.get()
        if metrics is not None:
            metrics["requests"] += 1
            if retry:
                metrics["retries"] += 1

    def _record_bytes(self, count: int) -> None:
        metrics = self._operation_metrics.get()
        if metrics is not None:
            metrics["response_bytes"] += max(0, int(count))

    @staticmethod
    def _retry_policy(retry: bool) -> tuple[int, list[float]]:
        if not retry:
            return 1, []
        try:
            attempts = max(1, int(RETRY_CONFIG.get("max_attempts", 3)))
        except (TypeError, ValueError):
            attempts = 3
        raw_delays = RETRY_CONFIG.get("delays") or []
        delays: list[float] = []
        for value in raw_delays:
            try:
                delays.append(max(0.0, float(value)))
            except (TypeError, ValueError):
                delays.append(0.0)
        return attempts, delays

    def _safe_detail(self, value: Any) -> str:
        detail = " ".join(str(value or "").split())[:240]
        secrets = (
            self.api_token,
            self.server.get("api_token"),
            self.server.get("password"),
        )
        for secret in secrets:
            secret_text = str(secret or "")
            if secret_text:
                detail = detail.replace(secret_text, "[redacted]")
        return detail

    @staticmethod
    def _is_expected_missing_client_lookup(error: PanelRequestError) -> bool:
        """Return whether a client lookup explicitly confirmed absence."""
        if not isinstance(error, PanelRejectedError):
            return False
        endpoint = str(error.endpoint or "")
        if not endpoint.startswith("/panel/api/clients/get/"):
            return False
        detail = str(error.detail or "").casefold()
        return any(
            marker in detail
            for marker in ("not found", "does not exist", "no client")
        )

    def _log_failure(self, error: PanelRequestError) -> None:
        if self._is_expected_missing_client_lookup(error):
            return
        exc_info = (
            (type(error), error, error.__traceback__)
            if error.__traceback__ is not None
            else None
        )
        logger.error(
            "panel_request_failed server_id=%s endpoint=%s kind=%s status=%s detail=%s",
            self.server_id,
            error.endpoint,
            error.kind.value,
            error.status,
            self._safe_detail(error.detail),
            exc_info=exc_info,
            stack_info=exc_info is None,
        )

    async def _sleep_before_retry(self, attempt: int, delays: list[float]) -> None:
        delay = delays[min(attempt, len(delays) - 1)] if delays else 0
        if delay:
            await asyncio.sleep(delay)

    @staticmethod
    def _response_error_kind(status: int) -> PanelErrorKind:
        if status == 401:
            return PanelErrorKind.UNAUTHORIZED
        if status == 403:
            return PanelErrorKind.FORBIDDEN
        if status == 404:
            return PanelErrorKind.NOT_FOUND
        if status >= 500:
            return PanelErrorKind.SERVER_ERROR
        return PanelErrorKind.CLIENT_ERROR

    async def _json_attempt(
        self,
        method: str,
        endpoint: str,
        *,
        token: str,
        data: Optional[Dict[str, Any]] = None,
        retry_attempt: bool = False,
    ) -> Dict[str, Any]:
        session = await self._ensure_session()
        self._record_attempt(retry=retry_attempt)
        try:
            async with session.request(
                method,
                f"{self.base_url}{endpoint}",
                json=data,
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Authorization": f"Bearer {token}",
                },
            ) as response:
                raw = await response.read()
                self._record_bytes(len(raw))
                status = response.status
        except asyncio.TimeoutError as exc:
            raise PanelRequestError(
                PanelErrorKind.TIMEOUT,
                endpoint=endpoint,
                detail="request timed out",
            ) from exc
        except aiohttp.ClientError as exc:
            raise PanelRequestError(
                PanelErrorKind.NETWORK,
                endpoint=endpoint,
                detail=type(exc).__name__,
            ) from exc

        if status != 200:
            raise PanelRequestError(
                self._response_error_kind(status),
                endpoint=endpoint,
                status=status,
            )
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=endpoint,
                status=status,
                detail="response is not JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=endpoint,
                status=status,
                detail="JSON response is not an object",
            )
        if payload.get("success") is not True:
            detail = self._safe_detail(payload.get("msg") or "success=false")
            if token:
                detail = detail.replace(token, "[redacted]")
            raise PanelRejectedError(
                endpoint=endpoint,
                status=status,
                detail=detail,
            )
        return payload

    async def _json_with_token(
        self,
        method: str,
        endpoint: str,
        *,
        token: str,
        data: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        attempts, delays = self._retry_policy(retry)
        last_error: Optional[PanelRequestError] = None
        for attempt in range(attempts):
            try:
                return await self._json_attempt(
                    method,
                    endpoint,
                    token=token,
                    data=data,
                    retry_attempt=attempt > 0,
                )
            except PanelRequestError as error:
                last_error = error
                if error.kind not in {
                    PanelErrorKind.TIMEOUT,
                    PanelErrorKind.NETWORK,
                    PanelErrorKind.SERVER_ERROR,
                } or attempt >= attempts - 1:
                    raise
                await self._sleep_before_retry(attempt, delays)
        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------
    # Cookie/CSRF bootstrap and one-shot 401 recovery
    # ------------------------------------------------------------------

    async def _cookie_json(
        self,
        method: str,
        endpoint: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        csrf_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = await self._ensure_session()
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token
        self._record_attempt()
        try:
            async with session.request(
                method,
                f"{self.base_url}{endpoint}",
                json=data,
                headers=headers,
            ) as response:
                raw = await response.read()
                self._record_bytes(len(raw))
                status = response.status
        except asyncio.TimeoutError as exc:
            raise PanelRequestError(
                PanelErrorKind.TIMEOUT,
                endpoint=endpoint,
                detail="bootstrap request timed out",
            ) from exc
        except aiohttp.ClientError as exc:
            raise PanelRequestError(
                PanelErrorKind.NETWORK,
                endpoint=endpoint,
                detail=f"bootstrap {type(exc).__name__}",
            ) from exc

        if status != 200:
            raise PanelRequestError(
                self._response_error_kind(status),
                endpoint=endpoint,
                status=status,
            )
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=endpoint,
                status=status,
                detail="bootstrap response is not JSON",
            ) from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            detail = payload.get("msg") if isinstance(payload, dict) else "invalid response"
            raise PanelRequestError(
                PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
                endpoint=endpoint,
                status=status,
                detail=self._safe_detail(detail),
            )
        return payload

    async def _reset_cookie_session(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _physical_panel_fields(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "web_base_path": str(self.server.get("web_base_path") or ""),
        }

    def _panel_auth_lock(self) -> asyncio.Lock:
        return _PANEL_AUTH_LOCKS.setdefault(self._panel_auth_key, asyncio.Lock())

    def _get_reusable_panel_token(self) -> Optional[str]:
        from database.db_servers import get_panel_recovery_api_token

        return get_panel_recovery_api_token(**self._physical_panel_fields())

    def _can_share_panel_auth(self) -> bool:
        return (
            self._has_password_credentials()
            or self.server.get("_shared_panel_auth") is True
        )

    def _has_shared_panel_auth_scope(self) -> bool:
        return self._can_share_panel_auth() and (
            self.server_id is not None
            or self.server.get("_shared_panel_auth") is True
        )

    def _load_reusable_panel_token(self) -> Optional[str]:
        return (
            self._get_reusable_panel_token()
            if self._has_shared_panel_auth_scope()
            else None
        )

    def _replace_matching_panel_tokens(
        self,
        expected_token: str,
        replacement_token: Optional[str],
    ) -> None:
        if not self._has_shared_panel_auth_scope():
            return
        from database.db_servers import replace_panel_api_token_for_endpoint

        replace_panel_api_token_for_endpoint(
            **self._physical_panel_fields(),
            expected_token=expected_token,
            replacement_token=replacement_token,
        )

    def _persist_api_token(self, token: Optional[str]) -> None:
        self.api_token = token or None
        self.server["api_token"] = token or None
        if self.server_id is not None:
            from database.db_servers import update_server_api_token

            update_server_api_token(int(self.server_id), token or None)

    def _persist_panel_version(self, version: str) -> None:
        self.panel_version = version
        self.server["panel_version"] = version
        self._capability_checked_monotonic = time.monotonic()
        if self.server_id is not None:
            from database.db_servers import update_server_panel_info

            update_server_panel_info(int(self.server_id), version)

    async def _clear_token_if_current(self, rejected_token: str) -> None:
        if self.api_token != rejected_token:
            return
        if self._can_share_panel_auth():
            self._replace_matching_panel_tokens(rejected_token, None)
        else:
            self._persist_api_token(None)
        self.api_token = None
        self.server["api_token"] = None
        self._validated_token = None
        self.is_authenticated = False

    def _accept_validated_token(
        self,
        token: str,
        version: str,
        *,
        replaced_token: Optional[str] = None,
    ) -> None:
        current_token_was_rejected = bool(
            replaced_token and self.api_token == replaced_token
        )
        if replaced_token:
            self._replace_matching_panel_tokens(replaced_token, token)
        if current_token_was_rejected and self._can_share_panel_auth():
            self.api_token = token
            self.server["api_token"] = token
        else:
            self._persist_api_token(token)
        self._persist_panel_version(version)
        self._validated_token = token
        self.is_authenticated = True
        self._panel_settings = None

    @staticmethod
    def _token_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = payload.get("obj") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("rows") or rows.get("tokens") or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def _bootstrap_candidate_token(self) -> str:
        if not self._has_password_credentials():
            raise PanelRequestError(
                PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
                endpoint="/login",
                detail="saved login and password are unavailable",
            )

        await self._reset_cookie_session()
        csrf_payload = await self._cookie_json("GET", "/csrf-token")
        csrf_token = csrf_payload.get("obj")
        if not isinstance(csrf_token, str) or not csrf_token:
            raise PanelRequestError(
                PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
                endpoint="/csrf-token",
                detail="CSRF token is missing",
            )

        await self._cookie_json(
            "POST",
            "/login",
            data={
                "username": str(self.server.get("login") or ""),
                "password": str(self.server.get("password") or ""),
            },
            csrf_token=csrf_token,
        )
        token_list = await self._cookie_json(
            "GET",
            "/panel/api/setting/apiTokens",
            csrf_token=csrf_token,
        )
        for row in self._token_rows(token_list):
            if str(row.get("name") or "") != BOT_API_TOKEN_NAME:
                continue
            try:
                row_id = int(row.get("id"))
            except (TypeError, ValueError):
                raise PanelRequestError(
                    PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
                    endpoint="/panel/api/setting/apiTokens",
                    detail="named token has no valid id",
                )
            delete_options = {"csrf_token": csrf_token}
            if isinstance(row.get("scope"), str) and row["scope"]:
                delete_options["data"] = {"expectedScope": row["scope"]}
            await self._cookie_json(
                "POST",
                f"/panel/api/setting/apiTokens/delete/{row_id}",
                **delete_options,
            )

        created = await self._cookie_json(
            "POST",
            "/panel/api/setting/apiTokens/create",
            data={"name": BOT_API_TOKEN_NAME},
            csrf_token=csrf_token,
        )
        obj = created.get("obj")
        candidate = obj.get("token") if isinstance(obj, dict) else None
        if not isinstance(candidate, str) or not candidate:
            raise PanelRequestError(
                PanelErrorKind.AUTH_BOOTSTRAP_FAILED,
                endpoint="/panel/api/setting/apiTokens/create",
                detail="created token plaintext is missing",
            )
        return candidate

    @staticmethod
    def _extract_version(payload: Dict[str, Any]) -> Optional[str]:
        obj = payload.get("obj")
        candidates = [obj, payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("currentVersion", "panelVersion", "version"):
                value = candidate.get(key)
                if isinstance(value, str) and parse_panel_version(value):
                    return value
        return None

    async def _validate_bearer_contract(self, token: str) -> str:
        status_payload = await self._json_with_token(
            "GET",
            "/panel/api/server/status",
            token=token,
            retry=True,
        )
        version = self._extract_version(status_payload)
        if version is None:
            update_payload = await self._json_with_token(
                "GET",
                "/panel/api/server/getPanelUpdateInfo",
                token=token,
                retry=True,
            )
            version = self._extract_version(update_payload)
        if version is None or not panel_version_at_least(
            version,
            MINIMUM_SUPPORTED_3X_UI_VERSION,
        ):
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_VERSION,
                endpoint="/panel/api/server/getPanelUpdateInfo",
                detail=f"detected version={version or 'unknown'}; minimum=3.3.0",
            )

        try:
            await self._json_with_token(
                "GET",
                "/panel/api/clients/list/paged?page=1&pageSize=1",
                token=token,
                retry=True,
            )
        except PanelRequestError as exc:
            if exc.kind in {PanelErrorKind.NOT_FOUND, PanelErrorKind.CLIENT_ERROR}:
                raise PanelRequestError(
                    PanelErrorKind.UNSUPPORTED_API,
                    endpoint=exc.endpoint,
                    status=exc.status,
                    detail="unified Clients API is unavailable",
                ) from exc
            raise
        topology = await self._json_with_token(
            "GET",
            "/panel/api/inbounds/list",
            token=token,
            retry=True,
        )
        if not isinstance(topology.get("obj"), list):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/inbounds/list",
                detail="topology is not a list",
            )
        return version

    async def _activate_candidate(
        self,
        candidate: str,
        *,
        replaced_token: Optional[str] = None,
    ) -> None:
        version = await self._validate_bearer_contract(candidate)
        self._accept_validated_token(
            candidate,
            version,
            replaced_token=replaced_token,
        )

    async def _recover_rejected_token_locked(
        self,
        rejected_token: str,
        rejection: PanelRequestError,
    ) -> str:
        """Recover one rejected token while the logical-client lock is held."""
        async with self._panel_auth_lock():
            if self.api_token and self.api_token != rejected_token:
                return self.api_token

            reusable = self._load_reusable_panel_token()
            rejected_reusable: Optional[str] = None
            if reusable and reusable != rejected_token:
                try:
                    version = await self._validate_bearer_contract(reusable)
                except PanelRequestError as exc:
                    if exc.kind != PanelErrorKind.UNAUTHORIZED:
                        raise
                    rejected_reusable = reusable
                else:
                    self._accept_validated_token(
                        reusable,
                        version,
                        replaced_token=rejected_token,
                    )
                    return reusable

            if not self._has_password_credentials():
                await self._clear_token_if_current(rejected_token)
                raise rejection

            try:
                candidate = await self._bootstrap_candidate_token()
                await self._activate_candidate(
                    candidate,
                    replaced_token=rejected_token,
                )
                if rejected_reusable:
                    self._replace_matching_panel_tokens(
                        rejected_reusable,
                        candidate,
                    )
            except Exception:
                await self._clear_token_if_current(rejected_token)
                if rejected_reusable:
                    self._replace_matching_panel_tokens(
                        rejected_reusable,
                        None,
                    )
                raise
            return candidate

    async def _bootstrap_or_reuse_token_locked(self) -> str:
        """Reuse a peer token or bootstrap one while the logical lock is held."""
        async with self._panel_auth_lock():
            reusable = self._load_reusable_panel_token()
            rejected_reusable: Optional[str] = None
            if reusable:
                try:
                    version = await self._validate_bearer_contract(reusable)
                except PanelRequestError as exc:
                    if exc.kind != PanelErrorKind.UNAUTHORIZED:
                        raise
                    rejected_reusable = reusable
                else:
                    self._accept_validated_token(reusable, version)
                    return reusable

            try:
                candidate = await self._bootstrap_candidate_token()
                await self._activate_candidate(
                    candidate,
                    replaced_token=rejected_reusable,
                )
            except Exception:
                if rejected_reusable:
                    self._replace_matching_panel_tokens(rejected_reusable, None)
                raise
            return candidate

    async def _recover_after_401(
        self,
        rejected_token: str,
        rejection: PanelRequestError,
    ) -> str:
        async with self._auth_lock:
            if self.api_token and self.api_token != rejected_token:
                return self.api_token
            return await self._recover_rejected_token_locked(
                rejected_token,
                rejection,
            )

    async def login(self) -> bool:
        if (
            self.is_authenticated
            and self.api_token
            and self._validated_token == self.api_token
        ):
            return True
        async with self._auth_lock:
            if (
                self.is_authenticated
                and self.api_token
                and self._validated_token == self.api_token
            ):
                return True
            if self.api_token:
                candidate = self.api_token
                try:
                    version = await self._validate_bearer_contract(candidate)
                except PanelRequestError as exc:
                    if exc.kind != PanelErrorKind.UNAUTHORIZED:
                        raise
                    await self._recover_rejected_token_locked(candidate, exc)
                    return True
                else:
                    self._persist_panel_version(version)
                    self._validated_token = candidate
                    self.is_authenticated = True
                    return True

            await self._bootstrap_or_reuse_token_locked()
            return True

    async def validate_connection(self) -> bool:
        try:
            return await self.login()
        except PanelRequestError as error:
            self._log_failure(error)
            raise

    def _capability_refresh_is_fresh(self) -> bool:
        checked_at = float(self._capability_checked_monotonic or 0.0)
        return bool(
            checked_at
            and time.monotonic() - checked_at < CAPABILITY_REFRESH_INTERVAL_SECONDS
        )

    async def refresh_capabilities(self, *, force: bool = False) -> bool:
        """Refresh the live panel version with a short per-client TTL."""
        await self.login()
        if not force and self._capability_refresh_is_fresh():
            return True
        async with self._capability_refresh_lock:
            if not force and self._capability_refresh_is_fresh():
                return True
            payload = await self._request("GET", "/panel/api/server/status")
            version = self._extract_version(payload)
            if version is None:
                payload = await self._request(
                    "GET",
                    "/panel/api/server/getPanelUpdateInfo",
                )
                version = self._extract_version(payload)
            if version is None or not panel_version_at_least(
                version,
                MINIMUM_SUPPORTED_3X_UI_VERSION,
            ):
                raise PanelRequestError(
                    PanelErrorKind.UNSUPPORTED_VERSION,
                    endpoint="/panel/api/server/getPanelUpdateInfo",
                    detail=(
                        f"detected version={version or 'unknown'}; minimum=3.3.0"
                    ),
                )
            self._persist_panel_version(version)
            return True

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        try:
            await self.login()
        except PanelRequestError as login_error:
            self._log_failure(login_error)
            raise
        token = self.api_token
        if not token:
            error = PanelRequestError(
                PanelErrorKind.UNAUTHORIZED,
                endpoint=endpoint,
                detail="Bearer token is unavailable",
            )
            self._log_failure(error)
            raise error
        try:
            return await self._json_with_token(
                method,
                endpoint,
                token=token,
                data=data,
                retry=retry,
            )
        except PanelRequestError as exc:
            if exc.kind != PanelErrorKind.UNAUTHORIZED:
                self._log_failure(exc)
                raise
            try:
                replacement = await self._recover_after_401(token, exc)
            except PanelRequestError as recovery_error:
                self._log_failure(recovery_error)
                raise
            try:
                return await self._json_with_token(
                    method,
                    endpoint,
                    token=replacement,
                    data=data,
                    retry=retry,
                )
            except PanelRequestError as replay_error:
                if replay_error.kind == PanelErrorKind.UNAUTHORIZED:
                    await self._clear_token_if_current(replacement)
                self._log_failure(replay_error)
                raise

    # ------------------------------------------------------------------
    # Topology and snapshots
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json_field(value: Any, default: Optional[Any] = None) -> Any:
        fallback = {} if default is None else default
        if value in (None, ""):
            return fallback.copy() if isinstance(fallback, (dict, list)) else fallback
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        return fallback.copy() if isinstance(fallback, (dict, list)) else fallback

    @classmethod
    def _normalize_inbound(cls, inbound: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(inbound)
        for field_name in JSON_INBOUND_FIELDS:
            value = normalized.get(field_name)
            if isinstance(value, (dict, list)):
                normalized[field_name] = json.dumps(value, ensure_ascii=False)
            elif value in (None, ""):
                normalized[field_name] = "{}"
        return normalized

    def _supports_mtproto(self) -> bool:
        return panel_version_at_least(
            self.panel_version,
            MTPROTO_MULTI_CLIENT_MIN_VERSION,
        )

    async def _all_inbounds(self) -> List[Dict[str, Any]]:
        payload = await self._request("GET", "/panel/api/inbounds/list")
        obj = payload.get("obj")
        if not isinstance(obj, list):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/inbounds/list",
                detail="obj is not a list",
            )
        inbounds = [
            self._normalize_inbound(item)
            for item in obj
            if isinstance(item, dict)
        ]
        if not self._supports_mtproto():
            inbounds = [item for item in inbounds if not is_mtproto_inbound(item)]
        return inbounds

    async def get_inbounds(self, include_ignored: bool = False) -> List[Dict[str, Any]]:
        inbounds = await self._all_inbounds()
        scoped = [
            item
            for item in inbounds
            if inbound_matches_group(item.get("tag"), self.inbound_group_id)
        ]
        return scoped if include_ignored else filter_visible_inbounds(scoped)

    async def get_nodes(self) -> List[Dict[str, Any]]:
        try:
            payload = await self._request(
                "GET",
                "/panel/api/nodes/list",
                retry=False,
            )
        except PanelRequestError as exc:
            if exc.kind == PanelErrorKind.NOT_FOUND:
                return []
            raise
        obj = payload.get("obj")
        return [item for item in obj if isinstance(item, dict)] if isinstance(obj, list) else []

    @staticmethod
    def _api_bool(value: Any, default: bool = True) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    async def _get_all_inbound_descriptors(self) -> List[PanelInboundDescriptor]:
        """Return the complete supported physical topology before logical scoping."""
        raw: Optional[List[Dict[str, Any]]]
        if self.inbound_group_id is not None:
            raw = await self._all_inbounds()
        else:
            raw = None
            try:
                payload = await self._request(
                    "GET",
                    "/panel/api/inbounds/options",
                    retry=False,
                )
                obj = payload.get("obj")
                if isinstance(obj, list):
                    raw = [item for item in obj if isinstance(item, dict)]
            except PanelRequestError as exc:
                if exc.kind != PanelErrorKind.NOT_FOUND:
                    raise
            if raw is None:
                raw = await self._all_inbounds()

        descriptors = [
            descriptor
            for descriptor in (build_inbound_descriptor(item) for item in raw)
            if descriptor is not None
            and (self._supports_mtproto() or descriptor.protocol != "mtproto")
        ]
        node_ids = {
            item.node_id for item in descriptors if item.node_id not in (None, 0)
        }
        if node_ids:
            nodes = await self.get_nodes()
            enabled_by_id: Dict[int, bool] = {}
            for node in nodes:
                try:
                    node_id = int(node.get("id"))
                except (AttributeError, TypeError, ValueError):
                    continue
                enabled_by_id[node_id] = self._api_bool(node.get("enable"), True)
            if enabled_by_id:
                descriptors = [
                    replace(item, node_enabled=enabled_by_id[item.node_id])
                    if item.node_id in enabled_by_id
                    else item
                    for item in descriptors
                ]
        return descriptors

    def _descriptor_is_in_scope(self, descriptor: PanelInboundDescriptor) -> bool:
        return inbound_matches_group(descriptor.tag, self.inbound_group_id)

    async def get_inbound_descriptors(
        self,
        *,
        include_ignored: bool = False,
    ) -> List[PanelInboundDescriptor]:
        descriptors = [
            item
            for item in await self._get_all_inbound_descriptors()
            if self._descriptor_is_in_scope(item)
        ]
        return descriptors if include_ignored else [
            item for item in descriptors if not item.ignored
        ]

    @staticmethod
    def _split_record(record: Dict[str, Any]) -> tuple[Dict[str, Any], set[int]]:
        if not isinstance(record, dict):
            return {}, set()
        if isinstance(record.get("client"), dict):
            client = dict(record["client"])
            raw_ids = record.get("inboundIds") or client.get("inboundIds") or []
        else:
            client = dict(record)
            raw_ids = record.get("inboundIds") or []
        inbound_ids = {
            int(value) for value in raw_ids if str(value).lstrip("-").isdigit()
        } if isinstance(raw_ids, list) else set()
        return client, inbound_ids

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _traffic_used(cls, payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        if payload.get("up") is not None or payload.get("down") is not None:
            return cls._int(payload.get("up")) + cls._int(payload.get("down"))
        for key in ("trafficUsed", "used", "totalUsed"):
            if payload.get(key) is not None:
                return cls._int(payload.get(key))
        return None

    async def _get_client_record(self, email: str) -> Optional[Dict[str, Any]]:
        endpoint = f"/panel/api/clients/get/{urllib.parse.quote(email, safe='')}"
        try:
            payload = await self._request("GET", endpoint, retry=False)
        except PanelRejectedError as exc:
            if self._is_expected_missing_client_lookup(exc):
                return None
            raise
        return payload.get("obj") if isinstance(payload.get("obj"), dict) else None

    async def _list_client_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page = 1
        page_size = 200
        while True:
            query = urllib.parse.urlencode({"page": page, "pageSize": page_size})
            payload = await self._request(
                "GET",
                f"/panel/api/clients/list/paged?{query}",
            )
            obj = payload.get("obj")
            if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
                raise PanelRequestError(
                    PanelErrorKind.INVALID_RESPONSE,
                    endpoint="/panel/api/clients/list/paged",
                    detail="paged client list has no items",
                )
            items = [item for item in obj["items"] if isinstance(item, dict)]
            rows.extend(items)
            expected = self._int(obj.get("filtered", obj.get("total")), -1)
            if not items or (expected >= 0 and len(rows) >= expected) or len(items) < page_size:
                break
            page += 1
            if page > 10000:
                raise PanelRequestError(
                    PanelErrorKind.INVALID_RESPONSE,
                    endpoint="/panel/api/clients/list/paged",
                    detail="pagination did not terminate",
                )
        return rows

    def _inbound_sync_scopes(
        self, descriptors: Iterable[PanelInboundDescriptor],
    ) -> tuple[set[int], set[int], set[int]]:
        """Separate attachment targets, explicit exclusions and retained disabled ids."""
        available, excluded, disabled = set(), set(), set()
        for item in descriptors:
            if item.ignored:
                excluded.add(item.id)
            elif not item.available:
                disabled.add(item.id)
            elif self._descriptor_is_in_scope(item):
                available.add(item.id)
            else:
                excluded.add(item.id)
        return available, excluded, disabled

    async def get_sync_snapshot(self) -> PanelServerSnapshot:
        async with self.operation_metrics("sync_snapshot"):
            descriptors = await self._get_all_inbound_descriptors()
            available_ids, excluded_ids, disabled_ids = self._inbound_sync_scopes(
                descriptors
            )
            available = [item for item in descriptors if item.id in available_ids]
            reconcilable_ids = available_ids | excluded_ids
            unavailable_ids = {
                item.id for item in descriptors if item.id not in reconcilable_ids
            }
            snapshot = PanelServerSnapshot(
                inbounds=[item.as_inbound() for item in available],
                clients={},
                unavailable_inbound_ids=set(unavailable_ids),
            )
            for row in await self._list_client_rows():
                email = str(row.get("email") or "").strip()
                if not email:
                    continue
                raw_ids = row.get("inboundIds") or []
                attached = {
                    int(value) for value in raw_ids if str(value).lstrip("-").isdigit()
                } if isinstance(raw_ids, list) else set()
                usable_ids = attached.intersection(available_ids)
                out_of_scope_attached = attached.intersection(
                    excluded_ids
                )
                unavailable_attached = attached - usable_ids - out_of_scope_attached
                traffic = self._traffic_used(row.get("traffic"))
                if traffic is None:
                    traffic = self._traffic_used(row)
                snapshot.clients[email.lower()] = PanelClientState(
                    email=email,
                    client=dict(row),
                    inbound_ids=usable_ids,
                    out_of_scope_inbound_ids=out_of_scope_attached,
                    unavailable_inbound_ids=unavailable_attached,
                    disabled_inbound_ids=attached.intersection(disabled_ids),
                    traffic_used=traffic or 0,
                    traffic_known=True,
                    total_gb=self._int(row.get("totalGB", row.get("total"))),
                    expiry_time=self._int(row.get("expiryTime", row.get("expiry_time"))),
                    enable=self._api_bool(row.get("enable"), True),
                    sub_id=str(row.get("subId") or ""),
                    limit_ip=self._int(row.get("limitIp"), 1),
                    limit_hwid=self._int(row.get("limitHwid")),
                    reset=self._int(row.get("reset")),
                    details_complete=False,
                )
                snapshot.unavailable_inbound_ids.update(unavailable_attached)
            return snapshot

    async def hydrate_client_state(self, state: PanelClientState) -> PanelClientState:
        if state.details_complete:
            return state
        record = await self._get_client_record(state.email)
        if not record:
            raise PanelRequestError(
                PanelErrorKind.NOT_FOUND,
                endpoint=f"/panel/api/clients/get/{urllib.parse.quote(state.email, safe='')}",
                detail="client disappeared",
            )
        client, inbound_ids = self._split_record(record)
        known_available = set(state.inbound_ids)
        known_out_of_scope = set(state.out_of_scope_inbound_ids)
        state.disabled_inbound_ids.intersection_update(inbound_ids)
        state.client = client
        state.inbound_ids = inbound_ids.intersection(known_available)
        state.out_of_scope_inbound_ids = inbound_ids.intersection(
            known_out_of_scope
        )
        state.unavailable_inbound_ids = (
            inbound_ids - state.inbound_ids - state.out_of_scope_inbound_ids
        )
        state.placements = {value: dict(client) for value in state.inbound_ids}
        state.sub_id = str(client.get("subId") or state.sub_id)
        state.details_complete = True
        return state

    # ------------------------------------------------------------------
    # Logical client provisioning and mutations
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_tg_id(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _client_payload(
        self,
        record: Optional[Dict[str, Any]],
        *,
        email: str,
    ) -> Dict[str, Any]:
        source = dict(record or {})
        record_id = source.get("id")
        identifier = source.get("uuid") or (record_id if isinstance(record_id, str) else None)
        payload: Dict[str, Any] = {
            "email": email,
            "security": source.get("security", "auto"),
            "limitIp": self._int(source.get("limitIp"), 1),
            "totalGB": self._int(source.get("totalGB")),
            "expiryTime": self._int(source.get("expiryTime")),
            "enable": self._api_bool(source.get("enable"), True),
            "tgId": self._normalize_tg_id(source.get("tgId")),
            "subId": str(source.get("subId") or ""),
            "comment": str(source.get("comment") or ""),
            "reset": self._int(source.get("reset")),
        }
        if "limitHwid" in source:
            payload["limitHwid"] = self._int(source.get("limitHwid"))
        if identifier:
            payload["id"] = identifier
        for field_name in ("password", "auth", "flow", "secret", "adTag", "reverse"):
            if source.get(field_name) not in (None, ""):
                payload[field_name] = source[field_name]
        return payload

    async def _write_with_state_check(
        self,
        method: str,
        endpoint: str,
        *,
        data: Dict[str, Any],
        email: str,
        validator: Callable[[Optional[Dict[str, Any]]], bool],
    ) -> Optional[Dict[str, Any]]:
        attempts, delays = self._retry_policy(True)
        last_error: Optional[PanelRequestError] = None
        for attempt in range(attempts):
            try:
                await self._request(method, endpoint, data=data, retry=False)
                return await self._get_client_record(email)
            except PanelRejectedError:
                recovered = await self._get_client_record(email)
                if validator(recovered):
                    return recovered
                raise
            except PanelRequestError as exc:
                last_error = exc
                if exc.kind not in {
                    PanelErrorKind.TIMEOUT,
                    PanelErrorKind.NETWORK,
                    PanelErrorKind.SERVER_ERROR,
                }:
                    raise
                recovered = await self._get_client_record(email)
                if validator(recovered):
                    return recovered
                if attempt >= attempts - 1:
                    raise
                await self._sleep_before_retry(attempt, delays)
        if last_error:
            raise last_error
        return None

    async def _attach_one(self, email: str, inbound_id: int) -> Dict[str, Any]:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        encoded = urllib.parse.quote(email, safe="")

        def attached(record: Optional[Dict[str, Any]]) -> bool:
            return bool(record and inbound_id in self._split_record(record)[1])

        record = await self._write_with_state_check(
            "POST",
            f"/panel/api/clients/{encoded}/attach",
            data={"inboundIds": [inbound_id]},
            email=email,
            validator=attached,
        )
        if not record or not attached(record):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=f"/panel/api/clients/{encoded}/attach",
                detail="attachment was not confirmed",
            )
        return record

    async def _detach_one(self, email: str, inbound_id: int) -> Dict[str, Any]:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        encoded = urllib.parse.quote(email, safe="")

        def detached(record: Optional[Dict[str, Any]]) -> bool:
            return bool(record and inbound_id not in self._split_record(record)[1])

        record = await self._write_with_state_check(
            "POST",
            f"/panel/api/clients/{encoded}/detach",
            data={"inboundIds": [inbound_id]},
            email=email,
            validator=detached,
        )
        if not record or not detached(record):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=f"/panel/api/clients/{encoded}/detach",
                detail="detachment was not confirmed",
            )
        return record

    async def update_client_full(
        self,
        *,
        email: str,
        total_gb_bytes: Optional[int] = None,
        expiry_time_ms: Optional[int] = None,
        enable: Optional[bool] = None,
        limit_ip: Optional[int] = None,
        limit_hwid: Optional[int] = None,
        sub_id: Optional[str] = None,
        flow: Optional[str] = None,
        reset: Optional[int] = None,
        known_state: Optional[PanelClientState] = None,
    ) -> bool:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        if known_state is not None:
            await self.hydrate_client_state(known_state)
            if not known_state.update_inbound_ids:
                return False
            record = {
                "client": dict(known_state.client),
                "inboundIds": sorted(known_state.update_inbound_ids),
            }
        else:
            record = await self._get_client_record(email)
        if not record:
            return False
        client, inbound_ids = self._split_record(record)
        preserved_ids = set(inbound_ids)
        if known_state is not None:
            preserved_ids.update(known_state.out_of_scope_inbound_ids)
            preserved_ids.update(known_state.unavailable_inbound_ids)
        if (
            self.supports_client_hwids()
            and limit_hwid is None
            and "limitHwid" not in client
        ):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/clients/get/:email",
                detail="client record omits limitHwid required for a safe full update",
            )
        payload = self._client_payload(client, email=email)
        if total_gb_bytes is not None:
            payload["totalGB"] = max(0, int(total_gb_bytes))
        if expiry_time_ms is not None:
            payload["expiryTime"] = max(0, int(expiry_time_ms))
        if enable is not None:
            payload["enable"] = bool(enable)
        if limit_ip is not None:
            payload["limitIp"] = max(0, int(limit_ip))
        if limit_hwid is not None:
            if not self.supports_client_hwids():
                raise PanelRequestError(
                    PanelErrorKind.UNSUPPORTED_VERSION,
                    endpoint="/panel/api/clients/update/:email",
                    detail="client HWID limits require 3X-UI 3.7.0+",
                )
            payload["limitHwid"] = max(0, int(limit_hwid))
        if sub_id is not None:
            payload["subId"] = str(sub_id)
        if flow is not None:
            if flow:
                payload["flow"] = str(flow)
            else:
                payload.pop("flow", None)
        if reset is not None:
            payload["reset"] = max(0, int(reset))
        encoded = urllib.parse.quote(email, safe="")
        endpoint = f"/panel/api/clients/update/{encoded}"
        if inbound_ids:
            endpoint += "?inboundIds=" + ",".join(str(value) for value in sorted(inbound_ids))

        def update_confirmed(value: Optional[Dict[str, Any]]) -> bool:
            if not value:
                return False
            confirmed, confirmed_inbound_ids = self._split_record(value)
            if not preserved_ids.issubset(confirmed_inbound_ids):
                return False
            checks = [
                (self._int(confirmed.get("totalGB")), self._int(payload.get("totalGB"))),
                (self._int(confirmed.get("expiryTime")), self._int(payload.get("expiryTime"))),
                (
                    self._api_bool(confirmed.get("enable"), True),
                    self._api_bool(payload.get("enable"), True),
                ),
                (
                    self._int(confirmed.get("limitIp"), 1),
                    self._int(payload.get("limitIp"), 1),
                ),
                (str(confirmed.get("subId") or ""), str(payload.get("subId") or "")),
                (self._int(confirmed.get("reset")), self._int(payload.get("reset"))),
            ]
            if "limitHwid" in payload:
                checks.append((
                    self._int(confirmed.get("limitHwid")),
                    self._int(payload.get("limitHwid")),
                ))
            if any(actual != expected for actual, expected in checks):
                return False
            if flow is not None or "flow" in payload:
                return str(confirmed.get("flow") or "") == str(payload.get("flow") or "")
            return True

        confirmed_record = await self._write_with_state_check(
            "POST",
            endpoint,
            data=payload,
            email=email,
            validator=update_confirmed,
        )
        if not update_confirmed(confirmed_record):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=endpoint,
                detail="client update was not confirmed",
            )
        return True

    async def provision_client(
        self,
        *,
        email: str,
        total_gb: int = 0,
        total_gb_bytes: Optional[int] = None,
        expire_days: int = 0,
        expiry_time_ms: Optional[int] = None,
        limit_ip: int = 1,
        limit_hwid: int = 0,
        enable: bool = True,
        tg_id: str = "",
        sub_id: Optional[str] = None,
        inbound_ids: Optional[Iterable[int]] = None,
    ) -> PanelProvisionResult:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        if int(limit_hwid) > 0 and not self.supports_client_hwids():
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_VERSION,
                endpoint="/panel/api/clients/add",
                detail="client HWID limits require 3X-UI 3.7.0+",
            )
        async with self.operation_metrics("provision_client"):
            all_descriptors = await self._get_all_inbound_descriptors()
            descriptors = [
                item
                for item in all_descriptors
                if not item.ignored and self._descriptor_is_in_scope(item)
            ]
            by_id = {item.id: item for item in descriptors}
            available_ids, excluded_ids, disabled_ids = self._inbound_sync_scopes(
                all_descriptors
            )
            requested = (
                {int(value) for value in inbound_ids}
                if inbound_ids is not None
                else set(available_ids)
            )
            targets = requested.intersection(available_ids)
            failed: Dict[int, str] = {}
            for inbound_id in sorted(requested - targets):
                descriptor = by_id.get(inbound_id)
                failed[inbound_id] = (
                    descriptor.unavailable_reason
                    if descriptor is not None and not descriptor.available
                    else "Inbound is missing, ignored, or incompatible"
                )

            canonical_sub_id = str(sub_id or uuid.uuid4().hex)
            if not targets:
                return PanelProvisionResult(
                    email=email,
                    sub_id="",
                    attached_inbound_ids=set(),
                    failed_inbound_ids=failed,
                    complete=False,
                )
            total_bytes = (
                max(0, int(total_gb_bytes))
                if total_gb_bytes is not None
                else max(0, int(total_gb)) * 1024 ** 3
            )
            expiry = (
                max(0, int(expiry_time_ms))
                if expiry_time_ms is not None
                else (
                    0
                    if int(expire_days) == 0
                    else int((time.time() + int(expire_days) * 86400) * 1000)
                )
            )
            common_flow = next(
                (by_id[value].flow for value in sorted(targets) if by_id[value].flow),
                "",
            )

            record = await self._get_client_record(email)
            if record:
                client, attached = self._split_record(record)
                canonical_sub_id = str(client.get("subId") or canonical_sub_id)
            else:
                attached = set()
                client_payload: Dict[str, Any] = {
                    "email": email,
                    "totalGB": total_bytes,
                    "expiryTime": expiry,
                    "enable": bool(enable),
                    "limitIp": max(0, int(limit_ip)),
                    "tgId": self._normalize_tg_id(tg_id),
                    "subId": canonical_sub_id,
                    "reset": 0,
                }
                if self.supports_client_hwids():
                    client_payload["limitHwid"] = max(0, int(limit_hwid))
                if common_flow:
                    client_payload["flow"] = common_flow

                for first_target in sorted(targets):
                    def created(
                        value: Optional[Dict[str, Any]],
                        target: int = first_target,
                    ) -> bool:
                        return bool(value and target in self._split_record(value)[1])

                    try:
                        record = await self._write_with_state_check(
                            "POST",
                            "/panel/api/clients/add",
                            data={"client": client_payload, "inboundIds": [first_target]},
                            email=email,
                            validator=created,
                        )
                    except PanelRejectedError as exc:
                        failed[first_target] = self._safe_detail(exc)
                        continue
                    break
                if record:
                    _, attached = self._split_record(record)

            if record:
                for inbound_id in sorted(targets - attached):
                    try:
                        record = await self._attach_one(email, inbound_id)
                        _, attached = self._split_record(record)
                        failed.pop(inbound_id, None)
                    except PanelRequestError as exc:
                        failed[inbound_id] = self._safe_detail(exc)

                try:
                    update_client, update_attached = self._split_record(record)
                    update_state = PanelClientState(
                        email=email,
                        client=update_client,
                        inbound_ids=update_attached.intersection(available_ids),
                        out_of_scope_inbound_ids=update_attached.intersection(
                            excluded_ids
                        ),
                        unavailable_inbound_ids=(
                            update_attached
                            - available_ids
                            - excluded_ids
                        ),
                        disabled_inbound_ids=update_attached.intersection(disabled_ids),
                        details_complete=True,
                    )
                    updated = await self.update_client_full(
                        email=email,
                        total_gb_bytes=total_bytes,
                        expiry_time_ms=expiry,
                        enable=enable,
                        limit_ip=limit_ip,
                        limit_hwid=(
                            limit_hwid if self.supports_client_hwids() else None
                        ),
                        sub_id=canonical_sub_id,
                        flow=common_flow,
                        reset=0,
                        known_state=update_state,
                    )
                    if not updated:
                        raise PanelRequestError(
                            PanelErrorKind.INVALID_RESPONSE,
                            endpoint="/panel/api/clients/update/:email",
                            detail="client update was not confirmed",
                        )
                except PanelRequestError as exc:
                    for inbound_id in sorted(targets.intersection(attached)):
                        failed.setdefault(inbound_id, self._safe_detail(exc))

            record = await self._get_client_record(email)
            client, actual_ids = self._split_record(record or {})
            confirmed_sub_id = str(client.get("subId") or "").strip()
            attached = targets.intersection(actual_ids)
            for inbound_id in sorted(targets - attached):
                failed.setdefault(inbound_id, "Panel did not confirm the inbound attachment")
            if not confirmed_sub_id:
                for inbound_id in sorted(attached):
                    failed.setdefault(inbound_id, "Panel did not confirm the subscription id")

            snapshot = None
            if record:
                available_descriptors = [item for item in descriptors if item.available]
                state = PanelClientState(
                    email=email,
                    client=dict(client),
                    inbound_ids=actual_ids.intersection(available_ids),
                    out_of_scope_inbound_ids=actual_ids.intersection(
                        excluded_ids
                    ),
                    unavailable_inbound_ids=(
                        actual_ids - available_ids - excluded_ids
                    ),
                    disabled_inbound_ids=actual_ids.intersection(disabled_ids),
                    placements={value: dict(client) for value in actual_ids.intersection(available_ids)},
                    traffic_used=self._traffic_used(client.get("traffic")) or 0,
                    traffic_known=isinstance(client.get("traffic"), dict),
                    total_gb=self._int(client.get("totalGB")),
                    expiry_time=self._int(client.get("expiryTime")),
                    enable=self._api_bool(client.get("enable"), True),
                    sub_id=confirmed_sub_id,
                    limit_ip=self._int(client.get("limitIp"), 1),
                    limit_hwid=self._int(client.get("limitHwid")),
                    reset=self._int(client.get("reset")),
                    details_complete=True,
                )
                snapshot = PanelServerSnapshot(
                    inbounds=[item.as_inbound() for item in available_descriptors],
                    clients={email.lower(): state},
                    unavailable_inbound_ids=(disabled_ids | state.unavailable_inbound_ids),
                )
            return PanelProvisionResult(
                email=email,
                sub_id=confirmed_sub_id,
                attached_inbound_ids=set(attached),
                failed_inbound_ids=failed,
                complete=(
                    bool(targets)
                    and bool(confirmed_sub_id)
                    and attached == targets
                    and not failed
                ),
                snapshot=snapshot,
            )

    async def attach_client_inbounds(
        self,
        email: str,
        inbound_ids: Iterable[int],
        *,
        known_state: Optional[PanelClientState] = None,
        verify: bool = True,
    ) -> Optional[Dict[str, Any]]:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        record = await self._get_client_record(email)
        if not record:
            return None
        for inbound_id in sorted({int(value) for value in inbound_ids}):
            record = await self._attach_one(email, inbound_id)
        return record

    async def bulk_attach_clients(
        self,
        emails: Iterable[str],
        inbound_ids: Iterable[int],
    ) -> set[str]:
        confirmed: set[str] = set()
        targets = {int(value) for value in inbound_ids}
        for email in {str(value) for value in emails if str(value)}:
            try:
                record = await self.attach_client_inbounds(email, targets)
                if record and targets.issubset(self._split_record(record)[1]):
                    confirmed.add(email)
            except PanelRequestError:
                logger.exception("bulk attach failed server_id=%s email=%s", self.server_id, email)
        return confirmed

    async def bulk_detach_clients(
        self,
        emails: Iterable[str],
        inbound_ids: Iterable[int],
    ) -> set[str]:
        confirmed: set[str] = set()
        targets = {int(value) for value in inbound_ids}
        for email in {str(value) for value in emails if str(value)}:
            try:
                record = await self._get_client_record(email)
                if not record:
                    continue
                for inbound_id in sorted(targets.intersection(self._split_record(record)[1])):
                    record = await self._detach_one(email, inbound_id)
                if record and not targets.intersection(self._split_record(record)[1]):
                    confirmed.add(email)
            except PanelRequestError:
                logger.exception("bulk detach failed server_id=%s email=%s", self.server_id, email)
        return confirmed

    async def delete_client(self, email: str) -> bool:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        encoded = urllib.parse.quote(email, safe="")
        try:
            await self._request("POST", f"/panel/api/clients/del/{encoded}")
        except PanelRejectedError as exc:
            if any(marker in exc.detail.lower() for marker in ("not found", "does not exist")):
                return True
            raise
        return True

    async def delete_clients_by_email_on_server(self, email: str) -> int:
        record = await self._get_client_record(email)
        if not record:
            return 0
        await self.delete_client(email)
        return 1

    async def bulk_delete_clients(self, emails: Iterable[str]) -> int:
        count = 0
        for email in {str(value) for value in emails if str(value)}:
            try:
                count += int(await self.delete_client(email))
            except PanelRequestError:
                logger.exception("bulk delete failed server_id=%s email=%s", self.server_id, email)
        return count

    async def set_clients_enabled_by_email(self, email: str, enable: bool) -> int:
        return int(await self.update_client_full(email=email, enable=enable))

    async def bulk_set_clients_enabled(
        self,
        emails: Iterable[str],
        enable: bool,
    ) -> int:
        changed = 0
        for email in {str(value) for value in emails if str(value)}:
            try:
                changed += await self.set_clients_enabled_by_email(email, enable)
            except PanelRequestError:
                logger.exception("bulk enable failed server_id=%s email=%s", self.server_id, email)
        return changed

    async def rename_client_identity(self, old_email: str, new_email: str, expected_identity: dict) -> dict:
        """Recover an identity-only rename using a persisted complete snapshot."""
        from .identity import rename_client_identity
        return await rename_client_identity(self, old_email, new_email, expected_identity)

    async def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
        encoded = urllib.parse.quote(email, safe="")
        try:
            payload = await self._request(
                "GET",
                f"/panel/api/clients/traffic/{encoded}",
                retry=False,
            )
        except PanelRejectedError as exc:
            if any(marker in exc.detail.lower() for marker in ("not found", "does not exist")):
                return None
            raise
        obj = payload.get("obj")
        if not isinstance(obj, dict):
            return None
        traffic_used = self._traffic_used(obj) or 0
        return {
            **obj,
            "email": email,
            "up": self._int(obj.get("up")),
            "down": self._int(obj.get("down")),
            "total": self._int(obj.get("total", obj.get("totalGB"))),
            "expiryTime": self._int(obj.get("expiryTime", obj.get("expiry_time"))),
            "traffic_used": traffic_used,
        }

    async def reset_client_traffic(self, email: str) -> bool:
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        encoded = urllib.parse.quote(email, safe="")
        await self._request("POST", f"/panel/api/clients/resetTraffic/{encoded}")
        return True

    async def update_client_limit(self, email: str, total_gb_bytes: int) -> bool:
        return await self.update_client_full(
            email=email,
            total_gb_bytes=max(0, int(total_gb_bytes)),
        )

    async def extend_client_expiry(self, email: str, days: int) -> bool:
        record = await self._get_client_record(email)
        if not record:
            return False
        client, _ = self._split_record(record)
        current = self._int(client.get("expiryTime"))
        now_ms = int(time.time() * 1000)
        base = max(current, now_ms) if current > 0 else now_ms
        return await self.update_client_full(
            email=email,
            expiry_time_ms=base + int(days) * 86400 * 1000,
        )

    # ------------------------------------------------------------------
    # Status, settings, subscriptions, backup
    # ------------------------------------------------------------------

    async def get_server_status(self) -> Dict[str, Any]:
        payload = await self._request("GET", "/panel/api/server/status")
        return payload.get("obj") if isinstance(payload.get("obj"), dict) else {}

    async def get_online_clients_count(self) -> int:
        payload = await self._request(
            "POST",
            "/panel/api/clients/onlines",
            retry=False,
        )
        obj = payload.get("obj")
        return len(obj) if isinstance(obj, list) else 0

    async def get_stats(self) -> Dict[str, Any]:
        try:
            snapshot = await self.get_sync_snapshot()
            status = await self.get_server_status()
            raw_cpu = status.get("cpu")
            try:
                cpu = int(float(raw_cpu)) if raw_cpu is not None else None
            except (TypeError, ValueError):
                cpu = None
            return {
                "total_clients": len(snapshot.clients),
                "active_clients": sum(1 for item in snapshot.clients.values() if item.enable),
                "online_clients": await self.get_online_clients_count(),
                "total_traffic_bytes": sum(item.traffic_used for item in snapshot.clients.values()),
                "cpu_percent": cpu,
                "online": True,
            }
        except VPNAPIError as exc:
            logger.warning("panel stats unavailable server_id=%s: %s", self.server_id, exc)
            return {
                "total_clients": 0,
                "active_clients": 0,
                "online_clients": 0,
                "total_traffic_bytes": 0,
                "cpu_percent": None,
                "online": False,
                "error": str(exc),
            }

    async def get_panel_settings(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        if self._panel_settings is not None and not force_refresh:
            return self._panel_settings
        payload = await self._request("POST", "/panel/api/setting/all")
        obj = payload.get("obj")
        if not isinstance(obj, dict):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/setting/all",
                detail="settings obj is not an object",
            )
        self._panel_settings = obj
        return obj

    async def build_subscription_url(self, sub_id: str) -> Optional[str]:
        normalized_sub_id = str(sub_id or "").strip()
        if not normalized_sub_id:
            return None
        settings = await self.get_panel_settings()
        if not settings or not self._api_bool(settings.get("subEnable"), False):
            return None
        sub_uri = str(settings.get("subURI") or "").strip()
        if sub_uri:
            return f"{sub_uri.rstrip('/')}/{normalized_sub_id}"

        sub_domain = str(settings.get("subDomain") or "").strip() or self.host
        try:
            sub_port = int(settings.get("subPort") or 0)
        except (TypeError, ValueError):
            sub_port = 0
        sub_path = "/" + str(settings.get("subPath") or "").strip("/")
        sub_path = sub_path.rstrip("/") + "/"
        has_tls = bool(
            str(settings.get("subCertFile") or "").strip()
            and str(settings.get("subKeyFile") or "").strip()
        )
        scheme = "https" if has_tls else "http"
        default_port = 443 if has_tls else 80
        port_part = f":{sub_port}" if sub_port and sub_port != default_port else ""
        return f"{scheme}://{sub_domain}{port_part}{sub_path}{normalized_sub_id}"

    async def get_subscription_link(self, sub_id: str) -> Optional[str]:
        return await self.build_subscription_url(sub_id)

    def supports_client_external_links(self) -> bool:
        """Return whether the detected panel exposes per-client external links."""
        return panel_version_at_least(
            self.panel_version,
            CLIENT_EXTERNAL_LINKS_MIN_VERSION,
        )

    def supports_client_hwids(self) -> bool:
        """Return whether the detected panel exposes client HWID limits."""
        return panel_version_at_least(
            self.panel_version,
            CLIENT_HWID_LIMITS_MIN_VERSION,
        )

    @staticmethod
    def _optional_device_timestamp(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def get_client_devices(self, email: str) -> List[PanelClientDevice]:
        """Read registered client devices through the official HWID route."""
        if not self.supports_client_hwids():
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_API,
                endpoint="/panel/api/clients/hwids/:email",
                detail="client HWIDs require 3X-UI 3.7.0+",
            )
        normalized_email = str(email or "").strip()
        if not normalized_email:
            raise ValueError("email is required")
        encoded = urllib.parse.quote(normalized_email, safe="")
        payload = await self._request(
            "POST",
            f"/panel/api/clients/hwids/{encoded}",
        )
        rows = payload.get("obj")
        if not isinstance(rows, list):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/clients/hwids/:email",
                detail="obj is not a list",
            )
        devices: List[PanelClientDevice] = []
        for row in rows:
            if not isinstance(row, dict):
                raise PanelRequestError(
                    PanelErrorKind.INVALID_RESPONSE,
                    endpoint="/panel/api/clients/hwids/:email",
                    detail="device row is not an object",
                )
            raw_device_id = row.get("id")
            if (
                isinstance(raw_device_id, bool)
                or not str(raw_device_id or "").strip().isascii()
                or not str(raw_device_id or "").strip().isdecimal()
                or int(str(raw_device_id).strip()) <= 0
            ):
                raise PanelRequestError(
                    PanelErrorKind.INVALID_RESPONSE,
                    endpoint="/panel/api/clients/hwids/:email",
                    detail="device row has no positive numeric id",
                )
            device_id = str(int(str(raw_device_id).strip()))
            devices.append(PanelClientDevice(
                id=device_id,
                first_seen=self._optional_device_timestamp(row.get("firstSeen")),
                last_seen=self._optional_device_timestamp(row.get("lastSeen")),
                user_agent=str(row.get("userAgent") or "").strip(),
                device_os=str(row.get("deviceOs") or "").strip(),
                os_version=str(row.get("osVersion") or "").strip(),
                device_model=str(row.get("deviceModel") or "").strip(),
            ))
        return devices

    async def delete_client_device(self, email: str, device_id: str) -> bool:
        """Delete exactly one registered client device."""
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        if not self.supports_client_hwids():
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_API,
                endpoint="/panel/api/clients/hwids/:email/:id",
                detail="client HWIDs require 3X-UI 3.7.0+",
            )
        normalized_email = str(email or "").strip()
        normalized_device_id = str(device_id or "").strip()
        if (
            not normalized_email
            or not normalized_device_id.isascii()
            or not normalized_device_id.isdecimal()
            or int(normalized_device_id) <= 0
        ):
            raise ValueError("email and a positive numeric device_id are required")
        encoded_email = urllib.parse.quote(normalized_email, safe="")
        encoded_device_id = urllib.parse.quote(normalized_device_id, safe="")
        await self._request(
            "DELETE",
            f"/panel/api/clients/hwids/{encoded_email}/{encoded_device_id}",
        )
        return True

    async def get_client_external_links(self, email: str) -> List[Dict[str, Any]]:
        """Read the complete external-link collection for one logical client."""
        if not self.supports_client_external_links():
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_API,
                endpoint="/panel/api/clients/:email/externalLinks",
                detail="client external links require 3X-UI 3.4.0+",
            )
        normalized_email = str(email or "").strip()
        if not normalized_email:
            raise ValueError("email is required")
        record = await self._get_client_record(normalized_email)
        if record is None:
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/clients/get/:email",
                detail="client record is absent",
            )
        raw_links: Any = record.get("externalLinks", [])
        if isinstance(raw_links, str):
            try:
                raw_links = json.loads(raw_links)
            except (TypeError, json.JSONDecodeError) as exc:
                raise PanelRequestError(
                    PanelErrorKind.INVALID_RESPONSE,
                    endpoint="/panel/api/clients/get/:email",
                    detail="client externalLinks is not valid JSON",
                ) from exc
        if raw_links is None:
            raw_links = []
        if not isinstance(raw_links, list) or any(
            not isinstance(item, dict) for item in raw_links
        ):
            raise PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint="/panel/api/clients/get/:email",
                detail="client externalLinks is not a list of objects",
            )
        return [dict(item) for item in raw_links]

    async def replace_client_external_links(
        self,
        email: str,
        links: Iterable[Dict[str, Any]],
    ) -> bool:
        """Replace all external links after callers have merged foreign rows."""
        from database.requests import assert_panel_identity_ready
        assert_panel_identity_ready(self.server, email)
        if not self.supports_client_external_links():
            raise PanelRequestError(
                PanelErrorKind.UNSUPPORTED_API,
                endpoint="/panel/api/clients/:email/externalLinks",
                detail="client external links require 3X-UI 3.4.0+",
            )
        normalized_email = str(email or "").strip()
        if not normalized_email:
            raise ValueError("email is required")
        normalized_links: List[Dict[str, Any]] = []
        for item in links:
            if not isinstance(item, dict):
                raise ValueError("external link rows must be objects")
            normalized_links.append(dict(item))
        encoded = urllib.parse.quote(normalized_email, safe="")
        await self._request(
            "POST",
            f"/panel/api/clients/{encoded}/externalLinks",
            data={"externalLinks": normalized_links},
        )
        return True

    @staticmethod
    def _detect_database_backup(data: bytes) -> Optional[PanelDatabaseBackup]:
        if data.startswith(b"SQLite format 3\x00"):
            return PanelDatabaseBackup(data=data, extension=".db", db_kind="sqlite")
        if data.startswith(b"PGDMP"):
            return PanelDatabaseBackup(data=data, extension=".dump", db_kind="postgres")
        return None

    async def _bytes_with_token(
        self,
        endpoint: str,
        *,
        token: str,
        retry: bool = True,
    ) -> bytes:
        attempts, delays = self._retry_policy(retry)
        for attempt in range(attempts):
            session = await self._ensure_session()
            self._record_attempt(retry=attempt > 0)
            try:
                async with session.get(
                    f"{self.base_url}{endpoint}",
                    headers={
                        "Accept": "application/octet-stream",
                        "X-Requested-With": "XMLHttpRequest",
                        "Authorization": f"Bearer {token}",
                    },
                ) as response:
                    data = await response.read()
                    self._record_bytes(len(data))
                    status = response.status
            except asyncio.TimeoutError as exc:
                error = PanelRequestError(
                    PanelErrorKind.TIMEOUT,
                    endpoint=endpoint,
                    detail="backup request timed out",
                )
                error.__cause__ = exc
            except aiohttp.ClientError as exc:
                error = PanelRequestError(
                    PanelErrorKind.NETWORK,
                    endpoint=endpoint,
                    detail=type(exc).__name__,
                )
                error.__cause__ = exc
            else:
                if status == 200:
                    return data
                error = PanelRequestError(
                    self._response_error_kind(status),
                    endpoint=endpoint,
                    status=status,
                )
            if error.kind not in {
                PanelErrorKind.TIMEOUT,
                PanelErrorKind.NETWORK,
                PanelErrorKind.SERVER_ERROR,
            } or attempt >= attempts - 1:
                raise error
            await self._sleep_before_retry(attempt, delays)
        raise PanelRequestError(PanelErrorKind.NETWORK, endpoint=endpoint)

    async def get_database_backup(self) -> PanelDatabaseBackup:
        endpoint = "/panel/api/server/getDb"
        try:
            await self.login()
        except PanelRequestError as login_error:
            self._log_failure(login_error)
            raise
        token = self.api_token
        if not token:
            error = PanelRequestError(
                PanelErrorKind.UNAUTHORIZED,
                endpoint=endpoint,
                detail="Bearer token is unavailable",
            )
            self._log_failure(error)
            raise error
        try:
            data = await self._bytes_with_token(endpoint, token=token)
        except PanelRequestError as exc:
            if exc.kind != PanelErrorKind.UNAUTHORIZED:
                self._log_failure(exc)
                raise
            try:
                replacement = await self._recover_after_401(token, exc)
            except PanelRequestError as recovery_error:
                self._log_failure(recovery_error)
                raise
            try:
                data = await self._bytes_with_token(endpoint, token=replacement)
            except PanelRequestError as replay_error:
                if replay_error.kind == PanelErrorKind.UNAUTHORIZED:
                    await self._clear_token_if_current(replacement)
                self._log_failure(replay_error)
                raise
        backup = self._detect_database_backup(data)
        if backup is None:
            error = PanelRequestError(
                PanelErrorKind.INVALID_RESPONSE,
                endpoint=endpoint,
                detail="response is not a supported database backup",
            )
            self._log_failure(error)
            raise error
        return backup

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None
        self.is_authenticated = False
        self._validated_token = None


__all__ = [
    "BOT_API_TOKEN_NAME",
    "MTPROTO_MULTI_CLIENT_MIN_VERSION",
    "XUIClient",
]
