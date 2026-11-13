"""Apply presentation defaults only to the new-installation migration boundary."""
from __future__ import annotations

import json

from localization import catalog


def initialize_presentation(conn, language: str) -> None:
    """Called before first activation; historical/custom installations are never reseeded."""
    texts = catalog('seed', language)
    for row in conn.execute('SELECT page_key,text_default,buttons_default FROM pages').fetchall():
        key = row['page_key']
        buttons = json.loads(row['buttons_default'] or '[]')
        for button in buttons:
            message = 'page.' + key + '.button.' + str(button.get('id', ''))
            if message in texts:
                button['label'] = texts[message]
        conn.execute('UPDATE pages SET text_default=?,buttons_default=? WHERE page_key=?',
                     (texts.get('page.' + key + '.text', row['text_default']),
                      json.dumps(buttons, ensure_ascii=False), key))
    for row in conn.execute('SELECT text_key FROM user_ui_texts').fetchall():
        key = row['text_key']
        conn.execute('UPDATE user_ui_texts SET text_default=? WHERE text_key=?', (texts['ui.' + key], key))
    for key, value in texts.items():
        if key.startswith('setting.'):
            conn.execute('UPDATE settings SET value=? WHERE key=?', (value, key.removeprefix('setting.')))
    names = [value for key, value in texts.items() if key.startswith('account_name.')]
    if names:
        conn.execute("UPDATE settings SET value=? WHERE key='account_name_pool'",
                     (json.dumps(names, ensure_ascii=False),))
    for table in ('tariff_groups', 'tariffs'):
        for key, value in texts.items():
            if key.startswith(table + '.') and key.endswith('.name'):
                conn.execute('UPDATE ' + table + ' SET name=? WHERE id=?', (value, int(key.split('.')[1])))
