"""Apply referral start parameters to an already registered user."""
import logging

from database.requests import (
    get_referral_attribution_window_hours,
    get_user_by_referral_code,
    set_user_referrer,
)

logger = logging.getLogger(__name__)


def attribute_start_referral(user: dict, *, is_new: bool, args: str | None) -> bool:
    """Return whether this contact assigned a referrer for the first time."""
    if user.get('is_banned') or not args or not args.startswith('ref_'):
        return False

    window_hours = get_referral_attribution_window_hours()
    if not is_new and window_hours <= 0:
        return False

    referrer = get_user_by_referral_code(args[4:])
    if not referrer or referrer['id'] == user['id']:
        return False

    assigned = set_user_referrer(
        user['id'],
        referrer['id'],
        is_new_registration=is_new,
        attribution_window_hours=window_hours,
    )
    if assigned:
        logger.info('User %s attributed to referrer %s', user['id'], referrer['id'])
    return assigned
