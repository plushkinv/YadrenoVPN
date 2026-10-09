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
    return value
