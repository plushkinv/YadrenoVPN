"""Versioned stock presentation data; runtime changes remain database settings."""

WEB_UI_DEFAULTS = {'title': 'Ядрёно VPN', 'logo': '', 'preset': 'clear', 'theme': 'light', 'accent': '',
                   'sync_interval_seconds': '300'}
WEB_CABINET_BUTTON_ID = 'open_web_cabinet'


def web_cabinet_button(row):
    return {'id': WEB_CABINET_BUTTON_ID, 'label': '🌐 Личный кабинет', 'color': 'secondary',
            'row': row, 'col': 0, 'is_hidden': True, 'action_type': 'web_app', 'action_value': '%web_app_url%'}
