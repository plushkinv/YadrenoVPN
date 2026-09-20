"""Read existing administrator-owned help content without executing Telegram hooks."""
import json
from urllib.parse import urlsplit

from database import requests as db
from core.content import safe_html


def help_content():
    page = db.get_page('help') or {}
    text = page.get('text_custom') if page.get('text_custom') is not None else page.get('text_default', '')
    raw = page.get('buttons_custom') if page.get('buttons_custom') is not None else page.get('buttons_default', '[]')
    try:
        buttons = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        buttons = []
    from bot.utils.telegram_links import get_telegram_link_domain
    domain = get_telegram_link_domain()
    links = []
    for button in buttons if isinstance(buttons, list) else []:
        if not isinstance(button, dict) or button.get('is_hidden') or button.get('action_type') != 'url':
            continue
        url = str(button.get('action_value') or '').replace('%telegram_link_domain%', domain)
        try:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                continue
        except ValueError:
            continue
        links.append({'label': button.get('label'), 'url': url})
    return {'content_html': safe_html(str(text or '').replace('%telegram_link_domain%', domain)), 'links': links}
