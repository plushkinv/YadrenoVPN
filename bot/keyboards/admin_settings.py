
from localization import text as _tr
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any, Optional

from .admin_misc import back_button, home_button, cancel_button, state_pair_buttons

def bot_settings_kb() -> InlineKeyboardMarkup:
    """Keyboard of the bot settings section."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.1'), callback_data='admin_update_bot'))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.2'), callback_data='admin_web'))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.3'), callback_data='admin_backup_create'))
    builder.row(InlineKeyboardButton(
        text=_tr('admin_settings.backup_video_guide'),
        url='https://youtu.be/zNtAzEcV-cI',
    ))
    builder.row(InlineKeyboardButton(
        text=_tr('admin_settings.bot_settings_kb.4'),
        callback_data='admin_device_limit',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.5'), callback_data='admin_edit_texts'))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.6'), callback_data='admin_logs_menu'))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.bot_settings_kb.7'), callback_data='admin_stop_bot'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def device_limit_mode_kb(mode: str) -> InlineKeyboardMarkup:
    """Keyboard for selecting the global client limit mode."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        mode == 'ip',
        _tr('system.render_device_limit_screen.1'),
        'admin_device_limit_set:ip',
        _tr('system.render_device_limit_screen.2'),
        'admin_device_limit_set:hwid',
        right_active_emoji='🟢',
    ))
    builder.row(back_button('admin_bot_settings'), home_button())
    return builder.as_markup()


def extensions_diagnostics_kb(
    enabled: bool,
    setting_buttons: Optional[List[Dict[str, str]]] = None,
    *,
    core_edit_blocked: bool = True,
) -> InlineKeyboardMarkup:
    """Extension overview controls with explicit loader and core protection states."""
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_settings.extensions_diagnostics_kb.1'),
        'admin_extensions_set:1',
        _tr('admin_settings.extensions_diagnostics_kb.2'),
        'admin_extensions_set:0',
    ))
    builder.row(*state_pair_buttons(
        core_edit_blocked,
        _tr('admin_settings.extensions_diagnostics_kb.3'),
        'admin_extensions_core_guard_set:1',
        _tr('admin_settings.extensions_diagnostics_kb.4'),
        'admin_extensions_core_guard_set:0',
    ))
    for button in setting_buttons or []:
        text = str(button.get('text') or '').strip()
        callback_data = str(button.get('callback_data') or '').strip()
        if text and callback_data:
            builder.row(InlineKeyboardButton(text=text, callback_data=callback_data))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.extensions_diagnostics_kb.5'), callback_data='admin_extensions_details'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()


def extensions_details_kb() -> InlineKeyboardMarkup:
    """Refresh detailed diagnostics or return to the extension overview."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_servers.servers_list_kb.1'), callback_data='admin_extensions_details'))
    builder.row(back_button('admin_extensions_diagnostics'), home_button())
    return builder.as_markup()


def custom_reset_preview_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset preview keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.custom_reset_preview_kb.1'), callback_data='admin_custom_reset_confirm'))
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_custom_reset_cancel'))
    return builder.as_markup()


def custom_reset_cancel_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset phrase input keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_custom_reset_cancel'))
    return builder.as_markup()


def custom_reset_done_kb() -> InlineKeyboardMarkup:
    """Hidden customization reset completion keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('admin_misc.admin_main_menu_kb.5'), callback_data='admin_bot_settings'))
    builder.row(home_button())
    return builder.as_markup()


def trial_settings_kb(enabled: bool, tariff_name: Optional[str]=None) -> InlineKeyboardMarkup:
    """
    Trial subscription control keyboard.
    
    Args:
        enabled: Whether trial subscription is enabled
        tariff_name: Name of the selected tariff or None
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_trial_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_trial_set:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.trial_settings_kb.1'), callback_data='admin_trial_edit_text'))
    tariff_label = tariff_name if tariff_name else _tr('broadcast.render_broadcast_menu.1')
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.trial_settings_kb.2', p0=f'{tariff_label}'), callback_data='admin_trial_select_tariff'))
    builder.row(back_button('admin_panel'), home_button())
    return builder.as_markup()

def trial_tariff_select_kb(tariffs: List[Dict[str, Any]], selected_id: Optional[int]=None) -> InlineKeyboardMarkup:
    """
    Keyboard for selecting a tariff for a trial subscription.
    
    Displays ordinary tariffs only.
    
    Args:
        tariffs: List of all tariffs (including inactive ones)
        selected_id: ID of the currently selected tariff
    """
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if tariff.get('system_type') is not None:
            continue
        status = '🟢' if tariff.get('is_active') else '⚪'
        is_selected = tariff['id'] == selected_id
        selected_suffix = _tr('admin_settings.trial_tariff_select_kb.1') if is_selected else ''
        duration = _tr('key_plans.duration_text.1') if int(tariff['duration_days']) == 0 else _tr('tariffs.admin_duration_text.1', p0=f"{tariff['duration_days']}", count=int(tariff['duration_days']))
        builder.row(InlineKeyboardButton(text=f"{status} {tariff['name']} ({duration}){selected_suffix}", callback_data=f"admin_trial_set_tariff:{tariff['id']}"))
    builder.row(back_button('admin_trial'), home_button())
    return builder.as_markup()

def trial_edit_text_cancel_kb() -> InlineKeyboardMarkup:
    """Keyboard for undoing trial subscription text editing."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_trial'))
    return builder.as_markup()

def referral_main_kb(enabled: bool, reward_type: str, levels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Main menu of the referral system.
    
    Args:
        enabled: Whether the system is enabled
        reward_type: Reward type ('days' or 'balance')
        levels: List of levels [{level_number, percent, enabled}, ...]
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_payments.wata_management_kb.1'),
        'admin_referral_set:1',
        _tr('admin_payments.wata_management_kb.2'),
        'admin_referral_set:0',
    ))
    if reward_type == 'days':
        type_text = _tr('admin_settings.referral_main_kb.1')
    else:
        type_text = _tr('admin_settings.referral_main_kb.2')
    builder.row(InlineKeyboardButton(text=type_text, callback_data='admin_referral_toggle_type'))
    for level in levels:
        level_num = level['level_number']
        percent = level['percent']
        is_enabled = level['enabled']
        status = '🟢' if is_enabled else '⚪'
        builder.row(InlineKeyboardButton(text=_tr('admin_settings.referral_main_kb.3', p0=f'{status}', p1=f'{level_num}', p2=f'{percent}'), callback_data=f'admin_referral_level:{level_num}'))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.referral_main_kb.4'), callback_data='admin_referral_conditions'))
    builder.row(back_button('admin_marketing'), home_button())
    return builder.as_markup()

def referral_level_kb(level_num: int, percent: int, enabled: bool) -> InlineKeyboardMarkup:
    """
    Level editing keyboard.
    
    Args:
        level_num: Level number (1-3)
        percent: Current percentage
        enabled: Whether the level is enabled
    """
    builder = InlineKeyboardBuilder()
    builder.row(*state_pair_buttons(
        enabled,
        _tr('admin_payments.wata_management_kb.1'),
        f'admin_referral_level_set:{level_num}:1',
        _tr('admin_payments.wata_management_kb.2'),
        f'admin_referral_level_set:{level_num}:0',
    ))
    builder.row(InlineKeyboardButton(text=_tr('admin_settings.referral_level_kb.1', p0=f'{percent}'), callback_data=f'admin_referral_level_percent:{level_num}'))
    builder.row(back_button('admin_referral'), home_button())
    return builder.as_markup()

def referral_back_kb() -> InlineKeyboardMarkup:
    """Keyboard to return to the referral system menu."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=_tr('system.extension_setting_cancel_kb.1'), callback_data='admin_referral'))
    return builder.as_markup()
