"""Static administrator help; placeholder resolution stays in placeholders.py."""

from localization import text as _tr

from bot.utils.text import escape_html


_EXAMPLE_NOTE = (
    _tr('placeholder_help.module.1')
)
_COMPATIBILITY_NOTE = (
    _tr('placeholder_help.module.2')
)


def _example(description: str, template: str, result_html: str) -> str:
    """Pair a literal template with a trusted, static rendered example."""
    return f"• {description}\n<code>{escape_html(template)}</code>\n→ {result_html}"


_KEY_FIELD_EXAMPLES = (
    (_tr('placeholder_help.module.3'), _tr('placeholder_help.module.4'), _tr('placeholder_help.module.5')),
    (_tr('placeholder_help.module.6'), _tr('placeholder_help.module.7'), _tr('placeholder_help.module.8')),
    (_tr('placeholder_help.module.9'), "%key(field=status)%", "🟢"),
    (_tr('placeholder_help.module.10'), "%key(field=traffic)%", _tr('placeholder_help.module.11')),
    (_tr('placeholder_help.module.12'), _tr('placeholder_help.module.13'), _tr('placeholder_help.module.14')),
    (_tr('placeholder_help.module.15'), _tr('placeholder_help.module.16'), _tr('placeholder_help.module.17')),
    (_tr('placeholder_help.module.18'), _tr('placeholder_help.module.19'), _tr('placeholder_help.module.20')),
    (_tr('admin_states.module.30'), _tr('placeholder_help.module.21'), _tr('placeholder_help.module.22')),
    (_tr('placeholder_help.module.23'), _tr('placeholder_help.module.24'), _tr('placeholder_help.module.25')),
    (
        _tr('placeholder_help.module.26'),
        _tr('placeholder_help.module.27'),
        _tr('placeholder_help.module.28'),
    ),
)
_KEY_FIELDS_HELP = "\n\n".join(_example(*example) for example in _KEY_FIELD_EXAMPLES)
_KEY_TIME_NOTE = (
    _tr('placeholder_help.module.29')
)

_KEY_LINK_HELP = "\n\n".join((
    _example(_tr('placeholder_help.module.30'), "%key_copy%",
             "<code>https://vpn.example/sub/demo</code>"),
    _example(_tr('placeholder_help.module.31'), "%key_link%", "https://vpn.example/sub/demo"),
    _example(_tr('placeholder_help.module.32'),
             "%client_import_url(client=incy)%", "https://vpn.example/open-client#token=…&client=incy"),
    _example(
        _tr('placeholder_help.module.33'),
        "https://app.example/import?url=%key_link_url%",
        "<code>https://app.example/import?url=https%3A%2F%2Fvpn.example%2Fsub%2Fdemo</code>",
    ),
))

_COUPON_HELP = "\n\n".join((
    _example(
        _tr('placeholder_help.module.34'), "%payment_coupon%",
        _tr('placeholder_help.module.35'),
    ),
    _example(_tr('placeholder_help.module.36'), _tr('placeholder_help.module.37'), _tr('placeholder_help.module.38')),
    _example(_tr('placeholder_help.module.39'),
             _tr('placeholder_help.module.40'), _tr('placeholder_help.module.41')),
    _example(_tr('placeholder_help.module.42'),
             _tr('placeholder_help.module.43'), _tr('placeholder_help.module.44')),
))

EDITOR_PLACEHOLDER_HELP = {
    'main': (
        _tr('placeholder_help.module.45')
        + _EXAMPLE_NOTE
        + _example(_tr('placeholder_help.module.46'), "%tariffs%",
                   _tr('placeholder_help.module.47'))
        + "\n\n"
        + _example(_tr('placeholder_help.module.48'), "%tariffs(group_id=1)%",
                   _tr('placeholder_help.module.49'))
        + _tr('placeholder_help.module.50')
        + _COMPATIBILITY_NOTE
    ),
    'key_delivery': (
        _tr('placeholder_help.module.51')
        + _EXAMPLE_NOTE
        + _tr('placeholder_help.module.52') + _KEY_LINK_HELP
        + _tr('placeholder_help.module.53')
        + _KEY_FIELDS_HELP + _KEY_TIME_NOTE
        + _tr('placeholder_help.module.54') + _COUPON_HELP
        + _tr('placeholder_help.module.55')
        + _COMPATIBILITY_NOTE
    ),
    'notification_text': (
        _tr('placeholder_help.module.56')
        + _EXAMPLE_NOTE
        + _tr('placeholder_help.module.57')
        + _KEY_FIELDS_HELP + _KEY_TIME_NOTE
        + _tr('placeholder_help.module.58')
        + _COMPATIBILITY_NOTE
    ),
    'referral': (
        _tr('placeholder_help.module.59')
        + _EXAMPLE_NOTE
        + _example(_tr('placeholder_help.module.60'), _tr('placeholder_help.module.61'),
                   _tr('placeholder_help.module.62'))
        + "\n\n"
        + _example(
            _tr('placeholder_help.module.63'),
            "https://app.example/share?url=%referral_link_url%",
            "<code>https://app.example/share?url=https%3A%2F%2Ft.me%2FExampleBot%3Fstart%3Dref_demo</code>",
        )
        + "\n\n"
        + _example(_tr('placeholder_help.module.64'),
                   "%referral_stats%", _tr('placeholder_help.module.65'))
        + _tr('placeholder_help.module.66')
        + _COMPATIBILITY_NOTE
    ),
}

BROADCAST_PLACEHOLDER_HINT = (
    _tr('placeholder_help.module.67')
    + _example(
        _tr('placeholder_help.module.68'),
        _tr('placeholder_help.module.69'),
        _tr('placeholder_help.module.70'),
    )
    + _tr('placeholder_help.module.71')
)
