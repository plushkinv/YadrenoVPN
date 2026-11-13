
from localization import text as _tr
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from bot.utils.telegram_links import build_telegram_link

def state_pair_buttons(
    is_left_active: bool,
    left_text: str,
    left_callback: str,
    right_text: str,
    right_callback: str,
    *,
    left_active_emoji: str = '🟢',
    right_active_emoji: str = '🔴',
):
    """Returns two status buttons with an active and an inactive indicator."""
    left_emoji = left_active_emoji if is_left_active else '⚪'
    right_emoji = '⚪' if is_left_active else right_active_emoji
    return (
        InlineKeyboardButton(text=f'{left_emoji} {left_text}', callback_data=left_callback),
        InlineKeyboardButton(text=f'{right_emoji} {right_text}', callback_data=right_callback),
    )

def back_button(callback: str='back') -> InlineKeyboardButton:
    """'Back' button."""
    return InlineKeyboardButton(text=_tr('system.extension_settings_kb.7'), callback_data=callback)

def home_button() -> InlineKeyboardButton:
    """'Home' button."""
    return InlineKeyboardButton(text=_tr('system.extension_settings_kb.8'), callback_data='start')

def cancel_button() -> InlineKeyboardButton:
    """'Cancel' button."""
    return InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_servers')

def cancel_kb(callback_data: str) -> InlineKeyboardMarkup:
    """Keyboard with 'Cancel' button."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data=callback_data))
    return builder.as_markup()

def back_and_home_kb(back_callback: str='back') -> InlineKeyboardMarkup:
    """Keyboard with 'Back' and 'Home' buttons."""
    builder = InlineKeyboardBuilder()
    builder.row(back_button(back_callback), home_button())
    return builder.as_markup()

def home_only_kb() -> InlineKeyboardMarkup:
    """Keyboard with only 'Home' button."""
    builder = InlineKeyboardBuilder()
    builder.row(home_button())
    return builder.as_markup()

def admin_main_menu_kb() -> InlineKeyboardMarkup:
    """Main menu of the admin panel."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.1'), callback_data='admin_servers'),
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.2'), callback_data='admin_payments')
    )
    builder.row(
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.3'), callback_data='admin_users'),
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.4'), callback_data='admin_marketing')
    )
    builder.row(
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.5'), callback_data='admin_bot_settings'),
        InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.6'), callback_data='admin_extensions_diagnostics')
    )
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_misc.admin_main_menu_kb.7'),
            callback_data='admin_yadreno_customization',
            style='primary',
        )
    )
    builder.row(
        InlineKeyboardButton(
            text='🤖 Yadreno Admin',
            callback_data='admin_yadreno',
            style='primary',
        )
    )
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.8'), callback_data='admin_author_support', style='success'))
    builder.row(home_button())
    return builder.as_markup()

def marketing_menu_kb() -> InlineKeyboardMarkup:
    """Menu of marketing tools."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.marketing_menu_kb.1'), callback_data='admin_broadcast'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.marketing_menu_kb.2'), callback_data='admin_referral'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.marketing_menu_kb.3'), callback_data='admin_promocodes'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.marketing_menu_kb.4'), callback_data='admin_coupons'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()

def admin_logs_menu_kb() -> InlineKeyboardMarkup:
    """Log download menu."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.admin_logs_menu_kb.1'), callback_data='admin_download_log_full'), InlineKeyboardButton(text=_tr('admin_misc.admin_logs_menu_kb.2'), callback_data='admin_download_log_errors'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.admin_logs_menu_kb.3'), callback_data='admin_send_log_to_yadreno'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.admin_logs_menu_kb.4'), callback_data='admin_clear_logs_confirm'))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()

def stop_bot_confirm_kb() -> InlineKeyboardMarkup:
    """Bot stop confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.stop_bot_confirm_kb.1'), callback_data='admin_stop_bot_confirm'))
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_bot_settings'))
    return builder.as_markup()

def force_overwrite_confirm_kb() -> InlineKeyboardMarkup:
    """Forced overwrite confirmation keypad."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.force_overwrite_confirm_kb.1'), callback_data='admin_force_overwrite_confirm'))
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.force_overwrite_confirm_kb.2'), callback_data='admin_bot_settings'))
    return builder.as_markup()

def update_confirm_kb(
    has_updates: bool = True,
    has_blocking: bool = False,
    is_beta_only: bool = False,
    has_rollback_points: bool = False,
) -> InlineKeyboardMarkup:
    """Bot update confirmation keyboard.
    
    Args:
        has_updates: Are there any updates available?
        has_blocking: Whether there is a blocking commit among the updates
        is_beta_only: Whether all available updates are beta versions
        has_rollback_points: Whether at least one verified rollback point exists
    """
    builder = InlineKeyboardBuilder()
    if has_updates:
        if has_blocking:
            button_text = _tr('admin_misc.update_confirm_kb.1')
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))
        elif is_beta_only:
            button_text = _tr('admin_misc.update_confirm_kb.2')
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))
        else:
            button_text = _tr('admin_misc.update_confirm_kb.1')
            callback = 'admin_update_bot_confirm'
            builder.row(InlineKeyboardButton(text=button_text, callback_data=callback))

    if has_rollback_points:
        builder.row(
            InlineKeyboardButton(
                text=_tr('admin_misc.update_confirm_kb.3'),
                callback_data='admin_update_rollback',
            )
        )
    
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.update_confirm_kb.4'), callback_data='admin_force_overwrite_confirm'))
    
    if has_updates:
        builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_bot_settings'))
    else:
        builder.row(InlineKeyboardButton(text=_tr('system.extension_settings_kb.7'), callback_data='admin_bot_settings'))
    return builder.as_markup()


def update_rollback_entry_kb(
    *,
    has_rollback_points: bool,
    back_callback: str = 'admin_bot_settings',
) -> InlineKeyboardMarkup:
    """Navigation for update error or blocked screens."""
    builder = InlineKeyboardBuilder()
    if has_rollback_points:
        builder.row(
            InlineKeyboardButton(
                text=_tr('admin_misc.update_confirm_kb.3'),
                callback_data='admin_update_rollback',
            )
        )
    builder.row(back_button(back_callback), home_button())
    return builder.as_markup()


def update_rollback_points_kb(points: List[Any]) -> InlineKeyboardMarkup:
    """Build the list of available pre-update rollback points."""
    builder = InlineKeyboardBuilder()
    for point in points:
        created = point.created_at.astimezone()
        builder.row(
            InlineKeyboardButton(
                text=(
                    f'↩️ {point.display_release} · '
                    f'{point.source_short_commit} · {created:%d.%m %H:%M}'
                ),
                callback_data=f'admin_rb_pick:{point.snapshot_id}',
            )
        )
    builder.row(back_button('admin_update_bot'), home_button())
    return builder.as_markup()


def update_rollback_confirm_kb(snapshot_id: str) -> InlineKeyboardMarkup:
    """Confirmation keyboard for a selected update rollback point."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_tr('admin_misc.update_rollback_confirm_kb.1'),
            callback_data=f'admin_rb_go:{snapshot_id}',
        )
    )
    builder.row(back_button('admin_update_rollback'), home_button())
    return builder.as_markup()

def author_support_kb() -> InlineKeyboardMarkup:
    """Keyboard for author support screen."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=_tr('admin_misc.author_support_kb.1'), url='https://yoomoney.ru/fundraise/1GJ73GGRJBC.260318'),
        InlineKeyboardButton(text='💰 USDT', url=build_telegram_link('Ya_SellerBot', 'item-40'))
    )
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()
