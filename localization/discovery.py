"""Read-only facts used by installed customization inspection."""
from localization import LANGUAGES, language


def describe():
    return {
        'installation_language': language(),
        'installation_language_mutable': False,
        'supported_languages': list(LANGUAGES),
        'telegram_storage': ['pages', 'user_ui_texts', 'settings'],
        'application_template': {
            'catalog_directory': 'src/i18n/locales',
            'runtime': 'src/i18n/runtime.ts',
            'sdk': '@ui/i18n',
            'settings_field': 'language',
            'settings_field_readonly': True,
            'application_language_override': 'setApplicationLanguage',
        },
        'system_catalogs_mutable': False,
    }
