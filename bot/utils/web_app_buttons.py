"""Narrow HTTPS Mini App button contract shared by editing and rendering."""
import re
from urllib.parse import urlsplit


def validate_web_app_url(value, *, template=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError('web_app requires an HTTPS URL')
    checked = re.sub(r'%web_app_url%', 'https://preview.invalid', value, flags=re.IGNORECASE) if template else value
    from bot.utils.placeholders import contains_placeholder
    if not template and contains_placeholder(checked):
        raise ValueError('web_app URL contains an unresolved placeholder')
    try:
        url = urlsplit(checked)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or '\\' in checked:
            raise ValueError()
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError('web_app requires an HTTPS URL without credentials') from None
    return value


def public_web_app_url():
    from web_api.settings import get_web_settings
    try:
        settings = get_web_settings()
        return settings.public_origin if settings.enabled else ''
    except ValueError:
        return ''
