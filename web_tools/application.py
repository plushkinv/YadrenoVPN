"""The signed application entry contract, independent of platform HTML and assets."""
from __future__ import annotations

import re

from web_tools.paths import relative_name


def read_application(files):
    from web_tools.package import _json
    value = _json(files['application.json'])
    if (not isinstance(value, dict) or set(value) != {'format_version', 'entry', 'styles', 'pages'}
            or type(value['format_version']) is not int or value['format_version'] != 1):
        raise ValueError('invalid application descriptor')
    def asset(name, suffix):
        if not isinstance(name, str) or relative_name(name) != name or not name.endswith(suffix) or name not in files:
            raise ValueError('invalid application resource')
    asset(value['entry'], '.js')
    if not isinstance(value['styles'], list) or len(value['styles']) > 200:
        raise ValueError('invalid application styles')
    for name in value['styles']:
        asset(name, '.css')
    if not isinstance(value['pages'], list) or not 1 <= len(value['pages']) <= 2000:
        raise ValueError('invalid application pages')
    seen = set()
    for page in value['pages']:
        if (not isinstance(page, dict) or not {'id', 'title'} <= set(page)
                or set(page) - {'id', 'title', 'preview_parameter'}
                or not isinstance(page['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', page['id'])
                or page['id'] in seen or not isinstance(page['title'], str) or len(page['title']) > 1024
                or 'preview_parameter' in page and (not isinstance(page['preview_parameter'], str)
                    or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,128}', page['preview_parameter']))):
            raise ValueError('invalid application page')
        seen.add(page['id'])
    _page_titles(files, seen)
    return value


def _page_titles(files, page_ids):
    """Validate optional labels without changing the v1 application descriptor."""
    from web_tools.package import _json
    if 'page-titles.json' not in files:
        return {}
    value = _json(files['page-titles.json'])
    if (not isinstance(value, dict) or set(value) != {'format_version', 'languages'}
            or type(value['format_version']) is not int or value['format_version'] != 1
            or not isinstance(value['languages'], dict) or len(value['languages']) > 100):
        raise ValueError('invalid localized page titles')
    for language, titles in value['languages'].items():
        if (not re.fullmatch(r'[a-z]{2}(?:-[A-Z]{2})?', language) or not isinstance(titles, dict)
                or set(titles) - page_ids or any(not isinstance(title, str) or len(title) > 1024 for title in titles.values())):
            raise ValueError('invalid localized page title')
    return value['languages']


def localized_pages(files, language='ru'):
    application = read_application(files)
    titles = _page_titles(files, {page['id'] for page in application['pages']}).get(language, {})
    return [{**page, 'title': titles.get(page['id'], page['title'])} for page in application['pages']]
