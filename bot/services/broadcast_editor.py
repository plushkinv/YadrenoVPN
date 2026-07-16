"""Durable staged editor used by the contextual Yadreno Admin skill."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from typing import Any, Callable, Optional

from bot.services.broadcast_content import (
    BROADCAST_KIND_MESSAGE,
    BROADCAST_KIND_POLL,
    normalize_broadcast_content,
)
from bot.services.broadcast_validation import (
    BroadcastValidationError,
    validate_broadcast_message,
    validate_generated_poll,
)
from database.requests import (
    apply_broadcast_editor_stage,
    compare_and_swap_broadcast_stage,
    count_users_for_broadcast,
    delete_broadcast_confirmation,
    delete_broadcast_editor_stage,
    get_broadcast_confirmation_raw,
    get_broadcast_editor_snapshot,
    insert_broadcast_stage_if_absent,
    pop_broadcast_confirmation_raw,
    set_broadcast_confirmation_raw,
)

BROADCAST_STAGE_TTL_SECONDS = 24 * 60 * 60
BROADCAST_CONFIRM_TTL_SECONDS = 10 * 60
BROADCAST_STAGE_SCHEMA_VERSION = 1

DEFAULT_BROADCAST_STYLE_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "tone": "friendly_professional",
    "address": "polite_you",
    "emoji_level": "medium",
    "length": "compact",
    "headline": "emoji_bold",
    "paragraphs": "short",
    "cta": "direct_calm",
    "use_lists": True,
    "custom_instructions": "",
}

BROADCAST_FILTER_LABELS = {
    "all": "Все пользователи",
    "active": "С активными ключами",
    "inactive": "Без активных ключей",
    "never_paid": "Никогда не покупали",
    "expired": "Ключ истёк",
}

_STYLE_ENUMS = {
    "tone": frozenset({
        "friendly_professional",
        "professional",
        "friendly",
        "empathetic",
        "formal",
        "informal",
    }),
    "address": frozenset({"polite_you", "informal_you", "neutral"}),
    "emoji_level": frozenset({"none", "low", "medium", "high"}),
    "length": frozenset({"compact", "standard", "detailed"}),
    "headline": frozenset({"none", "bold", "emoji_bold"}),
    "paragraphs": frozenset({"short", "standard"}),
    "cta": frozenset({"direct_calm", "direct", "soft", "none"}),
}
_STYLE_KEYS = frozenset(DEFAULT_BROADCAST_STYLE_PROFILE)


class BroadcastEditorError(ValueError):
    """Raised for invalid stage actions or stale local state."""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def validate_style_profile(data: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate a complete style profile or an agent-authored patch."""
    if not isinstance(data, dict):
        raise BroadcastEditorError("Профиль стиля должен быть объектом")
    unknown = set(data) - _STYLE_KEYS
    if unknown:
        raise BroadcastEditorError("Неизвестные поля стиля: " + ", ".join(sorted(unknown)))
    profile = dict(data) if partial else {**DEFAULT_BROADCAST_STYLE_PROFILE, **data}
    for key, allowed in _STYLE_ENUMS.items():
        if key in profile and profile[key] not in allowed:
            raise BroadcastEditorError(f"Недопустимое значение style.{key}")
    if "schema_version" in profile and profile["schema_version"] != 1:
        raise BroadcastEditorError("Поддерживается только schema_version=1")
    if "use_lists" in profile and not isinstance(profile["use_lists"], bool):
        raise BroadcastEditorError("style.use_lists должен быть boolean")
    if "custom_instructions" in profile:
        instructions = profile["custom_instructions"]
        if not isinstance(instructions, str):
            raise BroadcastEditorError("style.custom_instructions должен быть строкой")
        if len(instructions) > 1000:
            raise BroadcastEditorError("style.custom_instructions длиннее 1000 символов")
    return profile


def load_broadcast_style_profile(raw: Optional[str] = None) -> dict[str, Any]:
    """Load a valid style profile, falling back after damaged JSON."""
    if raw is None:
        raw = get_broadcast_editor_snapshot(0).get("style")
    try:
        data = json.loads(raw) if raw else {}
        return validate_style_profile(data)
    except (TypeError, json.JSONDecodeError, BroadcastEditorError):
        return copy.deepcopy(DEFAULT_BROADCAST_STYLE_PROFILE)


def style_profile_summary(profile: dict[str, Any]) -> str:
    """Return a short Russian description for the admin broadcast screen."""
    emoji_labels = {"none": "без эмодзи", "low": "мало эмодзи", "medium": "эмодзи умеренно", "high": "много эмодзи"}
    length_labels = {"compact": "кратко", "standard": "средняя длина", "detailed": "подробно"}
    tone_labels = {
        "friendly_professional": "дружелюбно и профессионально",
        "professional": "профессионально",
        "friendly": "дружелюбно",
        "empathetic": "эмпатично",
        "formal": "формально",
        "informal": "неформально",
    }
    return ", ".join((
        tone_labels.get(str(profile.get("tone")), str(profile.get("tone"))),
        emoji_labels.get(str(profile.get("emoji_level")), str(profile.get("emoji_level"))),
        length_labels.get(str(profile.get("length")), str(profile.get("length"))),
    ))


def _parse_content(raw: Optional[str]) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    try:
        return normalize_broadcast_content(json.loads(raw))
    except (TypeError, json.JSONDecodeError):
        return None


def _new_stage(snapshot: dict[str, Optional[str]]) -> dict[str, Any]:
    now = int(time.time())
    filter_key = str(snapshot.get("filter") or "all")
    if filter_key not in BROADCAST_FILTER_LABELS:
        filter_key = "all"
    return {
        "schema_version": BROADCAST_STAGE_SCHEMA_VERSION,
        "base_config_revision": _safe_int(snapshot.get("config_revision")),
        "stage_revision": 0,
        "created_at": now,
        "updated_at": now,
        "content": _parse_content(snapshot.get("content")),
        "filter": filter_key,
        "default_style_patch": {},
        "one_off_style_override": {},
        "dirty_fields": [],
        "style_rewrite_required": False,
    }


def _parse_stage(raw: Optional[str]) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    try:
        stage = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(stage, dict) or stage.get("schema_version") != BROADCAST_STAGE_SCHEMA_VERSION:
        return None
    if not isinstance(stage.get("dirty_fields"), list):
        return None
    if stage.get("filter") not in BROADCAST_FILTER_LABELS:
        return None
    stage["content"] = normalize_broadcast_content(stage.get("content"))
    try:
        stage["default_style_patch"] = validate_style_profile(
            stage.get("default_style_patch") or {}, partial=True
        )
        stage["one_off_style_override"] = validate_style_profile(
            stage.get("one_off_style_override") or {}, partial=True
        )
    except BroadcastEditorError:
        return None
    stage["base_config_revision"] = _safe_int(stage.get("base_config_revision"))
    stage["stage_revision"] = _safe_int(stage.get("stage_revision"))
    stage["created_at"] = _safe_int(stage.get("created_at"))
    stage["updated_at"] = _safe_int(stage.get("updated_at"))
    stage["style_rewrite_required"] = bool(stage.get("style_rewrite_required"))
    stage["dirty_fields"] = sorted({str(item) for item in stage["dirty_fields"]})
    return stage


def ensure_broadcast_editor_stage(telegram_id: int) -> tuple[dict[str, Any], dict[str, Optional[str]]]:
    """Return a durable non-expired stage, creating or rebasing when safe."""
    snapshot = get_broadcast_editor_snapshot(telegram_id)
    stage = _parse_stage(snapshot.get("stage"))
    now = int(time.time())
    expired = bool(stage and now - stage.get("updated_at", 0) >= BROADCAST_STAGE_TTL_SECONDS)
    config_revision = _safe_int(snapshot.get("config_revision"))
    clean_stale = bool(
        stage
        and not stage.get("dirty_fields")
        and stage.get("base_config_revision") != config_revision
    )
    if stage is None or expired or clean_stale:
        if snapshot.get("stage") is not None:
            delete_broadcast_editor_stage(telegram_id)
        candidate = _new_stage(snapshot)
        insert_broadcast_stage_if_absent(telegram_id, _json_dumps(candidate))
        snapshot = get_broadcast_editor_snapshot(telegram_id)
        stage = _parse_stage(snapshot.get("stage")) or candidate
    return stage, snapshot


def _effective_style(stage: dict[str, Any], snapshot: dict[str, Optional[str]]) -> dict[str, Any]:
    profile = load_broadcast_style_profile(snapshot.get("style"))
    profile.update(stage.get("default_style_patch") or {})
    profile.update(stage.get("one_off_style_override") or {})
    return validate_style_profile(profile)


def _validate_stage_content(content: Optional[dict[str, Any]]) -> None:
    if not content:
        raise BroadcastEditorError("Материал рассылки не подготовлен")
    if content.get("kind") == BROADCAST_KIND_MESSAGE:
        validate_broadcast_message(
            content.get("text"),
            has_photo=bool(content.get("photo_file_id")),
        )
        return
    if content.get("kind") == BROADCAST_KIND_POLL:
        if content.get("poll_source") == "generated":
            validate_generated_poll(content)
            return
        if not content.get("draft_chat_id") or not content.get("draft_message_id"):
            raise BroadcastEditorError("Импортированный опрос больше недоступен")
        return
    raise BroadcastEditorError("Неизвестный тип материала рассылки")


def _material_state(content: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not content:
        return {"kind": "none"}
    if content.get("kind") == BROADCAST_KIND_POLL:
        return {
            "kind": "poll",
            "question": str(content.get("question") or ""),
            "options": list(content.get("options") or []),
            "poll_type": str(content.get("poll_type") or "regular"),
            "is_anonymous": bool(content.get("is_anonymous", True)),
            "allows_multiple_answers": bool(content.get("allows_multiple_answers", False)),
            "generated": content.get("poll_source") == "generated",
        }
    return {
        "kind": "message",
        "text": str(content.get("text") or ""),
        "has_photo": bool(content.get("photo_file_id")),
    }


def _state_payload(
    stage: dict[str, Any],
    snapshot: dict[str, Optional[str]],
    *,
    status: str = "ok",
    changed_fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    filter_key = str(stage.get("filter") or "all")
    recipient_count = count_users_for_broadcast(filter_key)
    current_config_revision = _safe_int(snapshot.get("config_revision"))
    config_conflict = stage.get("base_config_revision") != current_config_revision
    validation_error = ""
    try:
        _validate_stage_content(stage.get("content"))
    except (BroadcastEditorError, BroadcastValidationError) as error:
        validation_error = str(error)
    effective_style = _effective_style(stage, snapshot)
    return {
        "status": status,
        "stage_revision": int(stage.get("stage_revision") or 0),
        "base_config_revision": int(stage.get("base_config_revision") or 0),
        "config_revision": current_config_revision,
        "config_conflict": config_conflict,
        "changed_fields": changed_fields or [],
        "dirty_fields": list(stage.get("dirty_fields") or []),
        "material": _material_state(stage.get("content")),
        "filter": {
            "key": filter_key,
            "label": BROADCAST_FILTER_LABELS[filter_key],
            "recipient_count": recipient_count,
        },
        "style": {
            "effective": effective_style,
            "summary": style_profile_summary(effective_style),
            "default_patch": dict(stage.get("default_style_patch") or {}),
            "one_off_override": dict(stage.get("one_off_style_override") or {}),
            "rewrite_required": bool(stage.get("style_rewrite_required")),
        },
        "validation_error": validation_error,
        "ready_to_launch": bool(
            not validation_error
            and not config_conflict
            and not stage.get("style_rewrite_required")
            and recipient_count > 0
        ),
    }


def get_broadcast_editor_state(telegram_id: int) -> dict[str, Any]:
    """Return compact aggregate state safe to send to the hub."""
    stage, snapshot = ensure_broadcast_editor_stage(telegram_id)
    return _state_payload(stage, snapshot)


def _conflict_payload(telegram_id: int, status: str = "conflict") -> dict[str, Any]:
    stage, snapshot = ensure_broadcast_editor_stage(telegram_id)
    return _state_payload(stage, snapshot, status=status)


def _mutate_stage(
    telegram_id: int,
    expected_stage_revision: int,
    mutator: Callable[[dict[str, Any]], list[str]],
) -> dict[str, Any]:
    stage, snapshot = ensure_broadcast_editor_stage(telegram_id)
    if stage["stage_revision"] != int(expected_stage_revision):
        return _state_payload(stage, snapshot, status="conflict")
    if stage["base_config_revision"] != _safe_int(snapshot.get("config_revision")):
        return _state_payload(stage, snapshot, status="config_conflict")

    updated = copy.deepcopy(stage)
    changed_fields = mutator(updated)
    updated["stage_revision"] = stage["stage_revision"] + 1
    updated["updated_at"] = int(time.time())
    success, _current_raw = compare_and_swap_broadcast_stage(
        telegram_id,
        stage["stage_revision"],
        _json_dumps(updated),
    )
    if not success:
        return _conflict_payload(telegram_id)
    refreshed = get_broadcast_editor_snapshot(telegram_id)
    return _state_payload(updated, refreshed, changed_fields=changed_fields)


def execute_broadcast_editor_action(telegram_id: int, args: dict[str, Any]) -> str:
    """Execute one allowlisted local editor action and return compact JSON."""
    action = str(args.get("action") or "")
    try:
        if action == "get_state":
            payload = get_broadcast_editor_state(telegram_id)
        else:
            if "expected_stage_revision" not in args:
                raise BroadcastEditorError("expected_stage_revision обязателен для изменения")
            expected = int(args["expected_stage_revision"])
            if action == "stage_message":
                payload = _stage_message(telegram_id, expected, args)
            elif action == "stage_poll":
                payload = _stage_poll(telegram_id, expected, args)
            elif action == "stage_filter":
                payload = _stage_filter(telegram_id, expected, args)
            elif action == "stage_style":
                payload = _stage_style(telegram_id, expected, args)
            else:
                raise BroadcastEditorError("Неизвестное действие редактора рассылок")
    except (BroadcastEditorError, BroadcastValidationError, TypeError, ValueError) as error:
        payload = {"status": "error", "error": str(error)}
    return _json_dumps(payload)


def stage_local_broadcast_photo(telegram_id: int, photo_file_id: str) -> dict[str, Any]:
    """Stage a Telegram-owned photo locally without exposing its file id to the hub."""
    file_id = str(photo_file_id or "").strip()
    if not 1 <= len(file_id) <= 2048:
        raise BroadcastEditorError("Некорректный Telegram file_id фотографии")
    stage, _snapshot = ensure_broadcast_editor_stage(telegram_id)

    def mutate(updated: dict[str, Any]) -> list[str]:
        current = updated.get("content") or {}
        current_text = (
            str(current.get("text") or "")
            if current.get("kind") == BROADCAST_KIND_MESSAGE
            else ""
        )
        updated["content"] = {
            "kind": BROADCAST_KIND_MESSAGE,
            "text": current_text,
            "photo_file_id": file_id,
        }
        dirty = set(updated.get("dirty_fields") or [])
        dirty.add("material")
        updated["dirty_fields"] = sorted(dirty)
        return ["material"]

    return _mutate_stage(telegram_id, stage["stage_revision"], mutate)


def _stage_message(telegram_id: int, expected: int, args: dict[str, Any]) -> dict[str, Any]:
    media_action = str(args.get("media_action") or "keep")
    if media_action not in {"keep", "remove"}:
        raise BroadcastEditorError("media_action должен быть keep или remove")

    def mutate(stage: dict[str, Any]) -> list[str]:
        current = stage.get("content") or {}
        photo_file_id = (
            current.get("photo_file_id")
            if media_action == "keep" and current.get("kind") == BROADCAST_KIND_MESSAGE
            else None
        )
        text = validate_broadcast_message(
            args.get("message_text"),
            has_photo=bool(photo_file_id),
        )
        stage["content"] = {
            "kind": BROADCAST_KIND_MESSAGE,
            "text": text,
            "photo_file_id": photo_file_id,
        }
        dirty = set(stage.get("dirty_fields") or [])
        dirty.add("material")
        stage["dirty_fields"] = sorted(dirty)
        stage["style_rewrite_required"] = False
        return ["material"]

    return _mutate_stage(telegram_id, expected, mutate)


def _stage_poll(telegram_id: int, expected: int, args: dict[str, Any]) -> dict[str, Any]:
    poll = validate_generated_poll(args.get("poll"))

    def mutate(stage: dict[str, Any]) -> list[str]:
        stage["content"] = poll
        dirty = set(stage.get("dirty_fields") or [])
        dirty.add("material")
        stage["dirty_fields"] = sorted(dirty)
        stage["style_rewrite_required"] = False
        return ["material"]

    return _mutate_stage(telegram_id, expected, mutate)


def _stage_filter(telegram_id: int, expected: int, args: dict[str, Any]) -> dict[str, Any]:
    filter_key = str(args.get("audience_filter") or "")
    if filter_key not in BROADCAST_FILTER_LABELS:
        raise BroadcastEditorError("Неизвестный фильтр получателей")

    def mutate(stage: dict[str, Any]) -> list[str]:
        stage["filter"] = filter_key
        dirty = set(stage.get("dirty_fields") or [])
        dirty.add("filter")
        stage["dirty_fields"] = sorted(dirty)
        return ["filter"]

    return _mutate_stage(telegram_id, expected, mutate)


def _stage_style(telegram_id: int, expected: int, args: dict[str, Any]) -> dict[str, Any]:
    scope = str(args.get("style_scope") or "")
    if scope not in {"default", "one_off"}:
        raise BroadcastEditorError("style_scope должен быть default или one_off")
    patch = validate_style_profile(args.get("style_patch"), partial=True)
    if not patch:
        raise BroadcastEditorError("style_patch не должен быть пустым")

    def mutate(stage: dict[str, Any]) -> list[str]:
        key = "default_style_patch" if scope == "default" else "one_off_style_override"
        stage[key] = {**stage.get(key, {}), **patch}
        dirty = set(stage.get("dirty_fields") or [])
        dirty.add("style" if scope == "default" else "style_override")
        stage["dirty_fields"] = sorted(dirty)
        if (stage.get("content") or {}).get("kind") == BROADCAST_KIND_MESSAGE:
            stage["style_rewrite_required"] = True
        return ["style"]

    return _mutate_stage(telegram_id, expected, mutate)


def broadcast_stage_is_dirty(telegram_id: int) -> bool:
    """Return whether an administrator has unapplied staged changes."""
    stage, _snapshot = ensure_broadcast_editor_stage(telegram_id)
    return bool(stage.get("dirty_fields"))


def discard_broadcast_editor_stage(telegram_id: int) -> None:
    """Discard all unsaved editor changes and any pending confirmation."""
    delete_broadcast_editor_stage(telegram_id)
    delete_broadcast_confirmation(telegram_id)


def save_broadcast_editor_stage(telegram_id: int) -> dict[str, Any]:
    """Atomically apply the stage to working content/filter/default style."""
    stage, snapshot = ensure_broadcast_editor_stage(telegram_id)
    if stage["base_config_revision"] != _safe_int(snapshot.get("config_revision")):
        return _state_payload(stage, snapshot, status="config_conflict")
    dirty_fields = set(stage.get("dirty_fields") or [])
    if not dirty_fields:
        return _state_payload(stage, snapshot, status="saved")
    if stage.get("content") is not None or "material" in dirty_fields:
        try:
            _validate_stage_content(stage.get("content"))
        except (BroadcastEditorError, BroadcastValidationError) as error:
            return {"status": "error", "error": str(error)}
    if stage.get("style_rewrite_required"):
        return {
            "status": "error",
            "error": "После изменения стиля нужно переработать текущий текст",
        }

    current_style = load_broadcast_style_profile(snapshot.get("style"))
    default_patch = dict(stage.get("default_style_patch") or {})
    raw_style = None
    if default_patch:
        current_style.update(default_patch)
        raw_style = _json_dumps(validate_style_profile(current_style))

    next_config_revision = stage["base_config_revision"] + 1
    saved_stage = copy.deepcopy(stage)
    saved_stage["stage_revision"] += 1
    saved_stage["base_config_revision"] = next_config_revision
    saved_stage["updated_at"] = int(time.time())
    saved_stage["dirty_fields"] = []
    saved_stage["default_style_patch"] = {}
    result = apply_broadcast_editor_stage(
        telegram_id,
        expected_stage_revision=stage["stage_revision"],
        expected_config_revision=stage["base_config_revision"],
        raw_content=_json_dumps(stage["content"]),
        filter_key=str(stage["filter"]),
        raw_style=raw_style,
        raw_saved_stage=_json_dumps(saved_stage),
    )
    if result.get("status") != "ok":
        return _conflict_payload(telegram_id, str(result.get("status") or "conflict"))
    refreshed = get_broadcast_editor_snapshot(telegram_id)
    return _state_payload(saved_stage, refreshed, status="saved")


def broadcast_material_hash(content: dict[str, Any]) -> str:
    """Return a stable digest without exposing broadcast material in callbacks."""
    return hashlib.sha256(_json_dumps(content).encode("utf-8")).hexdigest()


def create_broadcast_confirmation(telegram_id: int) -> dict[str, Any]:
    """Create a ten-minute one-time confirmation bound to current working state."""
    snapshot = get_broadcast_editor_snapshot(telegram_id)
    content = _parse_content(snapshot.get("content"))
    _validate_stage_content(content)
    filter_key = str(snapshot.get("filter") or "all")
    if filter_key not in BROADCAST_FILTER_LABELS:
        raise BroadcastEditorError("Неизвестный рабочий фильтр рассылки")
    recipient_count = count_users_for_broadcast(filter_key)
    if recipient_count <= 0:
        raise BroadcastEditorError("По выбранному фильтру нет получателей")
    now = int(time.time())
    confirmation = {
        "schema_version": 1,
        "token": secrets.token_urlsafe(18),
        "config_revision": _safe_int(snapshot.get("config_revision")),
        "material_hash": broadcast_material_hash(content or {}),
        "filter": filter_key,
        "recipient_count": recipient_count,
        "created_at": now,
        "expires_at": now + BROADCAST_CONFIRM_TTL_SECONDS,
    }
    set_broadcast_confirmation_raw(telegram_id, _json_dumps(confirmation))
    return confirmation


def consume_valid_broadcast_confirmation(telegram_id: int, token: str) -> Optional[dict[str, Any]]:
    """Validate all bound launch fields and consume the matching token once."""
    raw = get_broadcast_confirmation_raw(telegram_id)
    if not raw:
        return None
    try:
        confirmation = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        delete_broadcast_confirmation(telegram_id)
        return None
    if not isinstance(confirmation, dict) or confirmation.get("token") != token:
        return None
    if _safe_int(confirmation.get("expires_at")) <= int(time.time()):
        delete_broadcast_confirmation(telegram_id)
        return None

    snapshot = get_broadcast_editor_snapshot(telegram_id)
    content = _parse_content(snapshot.get("content"))
    filter_key = str(snapshot.get("filter") or "all")
    current = {
        "config_revision": _safe_int(snapshot.get("config_revision")),
        "material_hash": broadcast_material_hash(content or {}),
        "filter": filter_key,
        "recipient_count": count_users_for_broadcast(filter_key),
    }
    if any(confirmation.get(key) != value for key, value in current.items()):
        delete_broadcast_confirmation(telegram_id)
        return None
    consumed = pop_broadcast_confirmation_raw(telegram_id, token)
    return confirmation if consumed else None
