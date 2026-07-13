def get_panel_email_prefix(user: dict) -> str:
    """Returns the common email prefix of the client in the 3X-UI panel."""
    if user.get('username'):
        return f"user_{user['username']}_"
    return f"user_{user['telegram_id']}_"
