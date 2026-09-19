"""Telegram audio metadata for the existing Satellite file transport."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from bot.services.yadreno_admin import YadrenoAdminError

AudioKind = Literal["voice", "recording"]
_AUDIO_TYPES = {".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav"}
_MIME_SUFFIXES = {
    "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/opus": ".opus",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/vnd.wave": ".wav",
}
_AUDIO_SUFFIXES = frozenset(_AUDIO_TYPES) | {
    ".mp3", ".m4a", ".aac", ".flac", ".oga", ".wma", ".aif", ".aiff",
}


def message_audio_kind(message: Any) -> AudioKind | None:
    """Distinguish a spoken request from a recording, including documents."""
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "recording"
    document = getattr(message, "document", None)
    if document:
        mime = (document.mime_type or "").lower().split(";", 1)[0].strip()
        suffix = Path(document.file_name or "").suffix.lower()
        if mime.startswith("audio/") or mime == "application/ogg" or suffix in _AUDIO_SUFFIXES:
            return "recording"
    return None


def audio_upload_meta(message: Any) -> tuple[str, str, str] | None:
    """Return supported audio metadata without transcoding or interpreting speech."""
    kind = message_audio_kind(message)
    if kind is None:
        return None
    media = (
        getattr(message, "voice", None)
        or getattr(message, "audio", None)
        or message.document
    )
    name = Path(getattr(media, "file_name", None) or "").name.strip()
    mime = (getattr(media, "mime_type", None) or "").lower().split(";", 1)[0].strip()
    suffix = Path(name).suffix.lower() or _MIME_SUFFIXES.get(mime, "")
    if kind == "voice" and not mime and not suffix:
        suffix = ".ogg"
    if suffix not in _AUDIO_TYPES:
        raise YadrenoAdminError(
            "Unsupported Telegram audio format",
            user_message="Этот формат аудио не поддерживается. Отправьте запись OGG/Opus или WAV размером до 10 МБ.",
        )
    if not name or not Path(name).suffix:
        name = f"{kind}_{message.message_id}{suffix}"
    return media.file_id, name, _AUDIO_TYPES[suffix]
