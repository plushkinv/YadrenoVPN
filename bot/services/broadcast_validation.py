"""Local validation for agent-authored Telegram broadcast content."""

from __future__ import annotations

import html.entities
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from bot.utils.event_placeholders import render_event_placeholders
from bot.utils.text import TELEGRAM_CAPTION_LIMIT, TELEGRAM_TEXT_LIMIT


class BroadcastValidationError(ValueError):
    """Raised when staged broadcast content is not safe for Telegram."""


_ALLOWED_TAGS = frozenset({
    "a",
    "b",
    "blockquote",
    "code",
    "del",
    "em",
    "i",
    "ins",
    "pre",
    "s",
    "span",
    "strike",
    "strong",
    "tg-emoji",
    "tg-spoiler",
    "u",
})
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "tg"})
_PLACEHOLDER_RE = re.compile(r"%[^%\s]+%")
_LANGUAGE_CLASS_RE = re.compile(r"language-[A-Za-z0-9_+.-]{1,64}\Z")


class _TelegramHtmlParser(HTMLParser):
    """Validate supported tags/attributes while collecting visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            raise BroadcastValidationError(f"Telegram HTML: тег <{tag}> не поддерживается")
        self._validate_attrs(tag, attrs)
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raise BroadcastValidationError(f"Telegram HTML: самозакрывающийся тег <{tag}/> не поддерживается")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.stack or self.stack[-1] != tag:
            raise BroadcastValidationError(f"Telegram HTML: нарушено закрытие тега </{tag}>")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if any(char in data for char in "<>&"):
            raise BroadcastValidationError(
                "Telegram HTML: символы <, > и & в тексте нужно экранировать"
            )
        self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        raise BroadcastValidationError("Telegram HTML: комментарии не поддерживаются")

    def handle_decl(self, decl: str) -> None:
        raise BroadcastValidationError("Telegram HTML: декларации не поддерживаются")

    def handle_pi(self, data: str) -> None:
        raise BroadcastValidationError("Telegram HTML: инструкции обработки не поддерживаются")

    def unknown_decl(self, data: str) -> None:
        raise BroadcastValidationError("Telegram HTML: неизвестная декларация")

    def handle_entityref(self, name: str) -> None:
        if name not in html.entities.html5 and f"{name};" not in html.entities.html5:
            raise BroadcastValidationError(f"Telegram HTML: неизвестная сущность &{name};")
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        try:
            value = int(name[1:], 16) if name.lower().startswith("x") else int(name)
        except ValueError as error:
            raise BroadcastValidationError("Telegram HTML: некорректная числовая сущность") from error
        if not 0 <= value <= 0x10FFFF:
            raise BroadcastValidationError("Telegram HTML: числовая сущность вне Unicode")
        self.parts.append(f"&#{name};")

    def close_and_validate(self) -> str:
        self.close()
        if self.stack:
            raise BroadcastValidationError(
                f"Telegram HTML: не закрыт тег <{self.stack[-1]}>"
            )
        return unescape("".join(self.parts))

    @staticmethod
    def _validate_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key).lower(): value for key, value in attrs}
        if len(attr_map) != len(attrs):
            raise BroadcastValidationError("Telegram HTML: атрибут тега указан дважды")
        if tag == "a":
            if set(attr_map) != {"href"} or not attr_map.get("href"):
                raise BroadcastValidationError("Telegram HTML: ссылка требует один атрибут href")
            parsed = urlparse(str(attr_map["href"]))
            if parsed.scheme.lower() not in _ALLOWED_LINK_SCHEMES:
                raise BroadcastValidationError("Telegram HTML: разрешены только http, https и tg ссылки")
            return
        if tag == "span":
            if attr_map != {"class": "tg-spoiler"}:
                raise BroadcastValidationError("Telegram HTML: span разрешён только для tg-spoiler")
            return
        if tag == "tg-emoji":
            emoji_id = attr_map.get("emoji-id")
            if set(attr_map) != {"emoji-id"} or not str(emoji_id or "").isdigit():
                raise BroadcastValidationError("Telegram HTML: tg-emoji требует числовой emoji-id")
            return
        if tag == "code" and attr_map:
            class_name = str(attr_map.get("class") or "")
            if set(attr_map) != {"class"} or not _LANGUAGE_CLASS_RE.fullmatch(class_name):
                raise BroadcastValidationError("Telegram HTML: некорректный class у code")
            return
        if tag == "blockquote" and attr_map == {"expandable": None}:
            return
        if attr_map:
            raise BroadcastValidationError(f"Telegram HTML: атрибуты тега <{tag}> не поддерживаются")


def _placeholder_test_context() -> dict[str, Any]:
    sample = "Я" * 64
    return {
        "telegram_id": 99999999999999999999,
        "user_display_name": sample,
        "user_username": "@" + "u" * 32,
        "user_registered_at": "31.12.2099",
        "user_balance_text": sample,
        "key_name": sample,
        "key_days_left": "99999",
        "key_traffic_remaining_percent": "100",
        "key_traffic_used_text": sample,
        "key_traffic_limit_text": sample,
        "referral_name": sample,
        "referral_login": sample,
        "referral_telegram_id": 99999999999999999999,
        "referral_level": "999",
        "buyer_name": sample,
        "buyer_login": sample,
        "buyer_telegram_id": 99999999999999999999,
        "payment_tariff_name": sample,
        "payment_amount_text": sample,
        "payment_period_text": sample,
        "referral_reward_text": sample,
    }


def validate_broadcast_message(text: Any, *, has_photo: bool = False) -> str:
    """Validate Telegram HTML/placeholders and return normalized message text."""
    if not isinstance(text, str) or not text.strip():
        raise BroadcastValidationError("Текст рассылки не должен быть пустым")
    normalized = text.strip()
    rendered = render_event_placeholders(
        normalized,
        "broadcast",
        _placeholder_test_context(),
        mode="html",
    )
    unknown = sorted(set(_PLACEHOLDER_RE.findall(rendered)))
    if unknown:
        raise BroadcastValidationError(
            "Неизвестные плейсхолдеры: " + ", ".join(unknown[:5])
        )

    parser = _TelegramHtmlParser()
    try:
        parser.feed(rendered)
        visible_text = parser.close_and_validate()
    except BroadcastValidationError:
        raise
    except (ValueError, TypeError) as error:
        raise BroadcastValidationError(f"Некорректный Telegram HTML: {error}") from error

    limit = TELEGRAM_CAPTION_LIMIT if has_photo else TELEGRAM_TEXT_LIMIT
    if len(visible_text) > limit:
        kind = "подписи к фото" if has_photo else "сообщения"
        raise BroadcastValidationError(
            f"Текст длиннее лимита Telegram для {kind}: {len(visible_text)} из {limit}"
        )
    return normalized


def validate_generated_poll(data: Any) -> dict[str, Any]:
    """Validate and normalize a newly generated Telegram poll or quiz."""
    if not isinstance(data, dict):
        raise BroadcastValidationError("Параметры опроса должны быть объектом")
    question = str(data.get("question") or "").strip()
    if not 1 <= len(question) <= 300:
        raise BroadcastValidationError("Вопрос опроса должен содержать от 1 до 300 символов")

    raw_options = data.get("options")
    if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 12:
        raise BroadcastValidationError("В опросе должно быть от 2 до 12 вариантов ответа")
    options = [str(option).strip() for option in raw_options]
    if any(not 1 <= len(option) <= 100 for option in options):
        raise BroadcastValidationError("Каждый вариант ответа должен содержать от 1 до 100 символов")
    if len({option.casefold() for option in options}) != len(options):
        raise BroadcastValidationError("Варианты ответа не должны повторяться")

    poll_type = str(data.get("type") or data.get("poll_type") or "regular").lower()
    if poll_type not in {"regular", "quiz"}:
        raise BroadcastValidationError("Тип опроса должен быть regular или quiz")
    is_anonymous = bool(data.get("is_anonymous", True))
    allows_multiple = bool(data.get("allows_multiple_answers", False))
    normalized: dict[str, Any] = {
        "kind": "poll",
        "poll_source": "generated",
        "delivery_mode": "clean",
        "question": question,
        "options": options,
        "poll_type": poll_type,
        "is_anonymous": is_anonymous,
        "allows_multiple_answers": allows_multiple if poll_type == "regular" else False,
    }
    if poll_type == "quiz":
        try:
            correct_option_id = int(data.get("correct_option_id"))
        except (TypeError, ValueError) as error:
            raise BroadcastValidationError("Для викторины нужен correct_option_id") from error
        if not 0 <= correct_option_id < len(options):
            raise BroadcastValidationError("correct_option_id не соответствует вариантам ответа")
        explanation = str(data.get("explanation") or "").strip()
        if len(explanation) > 200:
            raise BroadcastValidationError("Пояснение викторины не должно превышать 200 символов")
        normalized["correct_option_id"] = correct_option_id
        if explanation:
            normalized["explanation"] = explanation
    return normalized
