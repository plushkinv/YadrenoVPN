"""Versioned stock presentation data; runtime changes remain database settings."""
from localization import legacy_text

WEB_UI_DEFAULTS = {'preset': 'clear', 'theme': 'light',
                   'sync_interval_seconds': '300'}
WEB_CABINET_BUTTON_ID = 'open_web_cabinet'


def web_cabinet_button(row):
    return {'id': WEB_CABINET_BUTTON_ID, 'label': legacy_text('web_ui_defaults.cabinet_button'), 'color': 'secondary',
            'row': row, 'col': 0, 'is_hidden': True, 'action_type': 'web_app', 'action_value': '%web_app_url%'}
