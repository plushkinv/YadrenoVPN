import uuid


PANEL_EMAIL_PREFIX = "user_"


def get_panel_email_prefix(user: dict) -> str:
    """Returns the common email prefix of the client in the 3X-UI panel."""
    if user.get('telegram_id') is None:
        return f"site_{user['id']}_"
    if user.get('username'):
        return f"{PANEL_EMAIL_PREFIX}{user['username']}_"
    return f"{PANEL_EMAIL_PREFIX}{user['telegram_id']}_"


def generate_unique_panel_email(
    user: dict,
    *,
    stable_identity: str | None = None,
) -> str:
    """Build a managed panel client identifier, optionally stable for retries."""
    if stable_identity:
        stable_suffix = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"yadrenovpn-panel:{stable_identity}",
        ).hex[:5]
        prefix = (f"site_{user['id']}_" if user.get('telegram_id') is None
                  else f"{PANEL_EMAIL_PREFIX}{user['telegram_id']}_")
        return prefix + stable_suffix
    return f"{get_panel_email_prefix(user)}{uuid.uuid4().hex[:5]}"


def is_managed_panel_email(email: object) -> bool:
    """Return whether a panel client identifier belongs to the bot."""
    if not isinstance(email, str):
        return False
    return email.strip().casefold().startswith((PANEL_EMAIL_PREFIX, 'site_'))


def is_managed_panel_binding(server_id: int | None, email: object) -> bool:
    """Keep the legacy prefix contract and admit only explicitly claimed foreign names."""
    if is_managed_panel_email(email):
        return True
    if server_id is None or not isinstance(email, str) or not email:
        return False
    from database.requests import is_imported_panel_binding
    return is_imported_panel_binding(int(server_id), email)


def is_managed_key(key) -> bool:
    return is_managed_panel_binding(key.get('server_id'), key.get('panel_email'))


__all__ = [
    "PANEL_EMAIL_PREFIX",
    "generate_unique_panel_email",
    "get_panel_email_prefix",
    "is_managed_panel_email",
    "is_managed_panel_binding",
    "is_managed_key",
]
