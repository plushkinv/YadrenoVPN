"""Domain policy points for custom extensions."""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

_POLICY_NAME_RE = re.compile(r'^[a-z][a-z0-9_.:-]{0,127}$')

PricingPolicy = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
PromoRewardPolicy = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
ReferralRewardPolicy = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]

PRICING_POLICIES: dict[str, PricingPolicy] = {}
PROMO_REWARD_POLICIES: dict[str, PromoRewardPolicy] = {}
REFERRAL_REWARD_POLICIES: dict[str, ReferralRewardPolicy] = {}

_ALLOWED_PRICING_KEYS = {
    'ok',
    'final_amount',
    'discount_amount',
    'label',
    'reason',
    'metadata',
    'stop',
}
_ALLOWED_PROMO_REWARD_KEYS = {
    'ok',
    'reward_days',
    'label',
    'reason',
    'metadata',
    'stop',
}
_ALLOWED_REFERRAL_REWARD_KEYS = {
    'ok',
    'reward_cents',
    'reward_days',
    'label',
    'reason',
    'metadata',
    'stop',
}


def register_pricing_policy(name: str, func: PricingPolicy, *, replace: bool = False) -> None:
    """Registers the pricing policy."""
    _register_policy(PRICING_POLICIES, name, func, replace=replace)


def register_promo_reward_policy(name: str, func: PromoRewardPolicy, *, replace: bool = False) -> None:
    """Registers future promotional reward policy."""
    _register_policy(PROMO_REWARD_POLICIES, name, func, replace=replace)


def register_referral_reward_policy(name: str, func: ReferralRewardPolicy, *, replace: bool = False) -> None:
    """Registers future referral reward policy."""
    _register_policy(REFERRAL_REWARD_POLICIES, name, func, replace=replace)


def apply_pricing_policies(
    quote: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Consistently applies pricing policies to quote."""
    result = dict(quote)
    result.setdefault('pricing_policies', [])
    _validate_quote_amounts(result)

    for name, policy in list(PRICING_POLICIES.items()):
        policy_context = dict(context)
        policy_context['quote'] = dict(result)
        try:
            raw_decision = policy(policy_context)
            if inspect.isawaitable(raw_decision):
                raise ValueError('pricing policy должна быть синхронной')
            decision = _normalize_pricing_decision(raw_decision)
        except Exception as e:
            logger.warning("Pricing policy '%s' пропущена из-за ошибки: %s", name, e)
            continue

        if not decision:
            continue

        entry = {'name': name}
        for key in ('label', 'reason', 'metadata'):
            if key in decision and decision[key] is not None:
                entry[key] = decision[key]

        if decision.get('ok') is False:
            result['ok'] = False
            result['unavailable_reason'] = (
                decision.get('reason') or 'Способ оплаты недоступен по правилам расширения.'
            )
            result['pricing_policies'].append(entry)
            result['pricing_policy'] = name
            break

        original_amount = result.get('original_amount', 0)
        if 'final_amount' in decision:
            final_amount = _normalize_amount(decision['final_amount'], 'final_amount')
            result['final_amount'] = final_amount
            result['discount_amount'] = original_amount - final_amount
            entry['final_amount'] = final_amount
        elif 'discount_amount' in decision:
            discount_amount = _normalize_amount(decision['discount_amount'], 'discount_amount')
            discount_amount = min(discount_amount, original_amount)
            final_amount = max(0, original_amount - discount_amount)
            result['discount_amount'] = discount_amount
            result['final_amount'] = final_amount
            entry['discount_amount'] = discount_amount
            entry['final_amount'] = final_amount

        result['pricing_policies'].append(entry)
        result['pricing_policy'] = name

        if decision.get('stop'):
            break

    return result


def apply_promo_reward_policies(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collects declarative promo rewards, which the kernel then applies."""
    rewards: list[dict[str, Any]] = []

    for name, policy in list(PROMO_REWARD_POLICIES.items()):
        try:
            raw_decision = policy(dict(context))
            if inspect.isawaitable(raw_decision):
                raise ValueError('promo reward policy должна быть синхронной')
            decision = _normalize_promo_reward_decision(raw_decision)
        except Exception as e:
            logger.warning("Promo reward policy '%s' пропущена из-за ошибки: %s", name, e)
            continue

        if not decision:
            continue

        entry = _policy_entry(name, decision)
        if decision.get('ok') is False:
            entry['ok'] = False
            rewards.append(entry)
            if decision.get('stop'):
                break
            continue

        if 'reward_days' in decision:
            reward_days = _normalize_amount(decision['reward_days'], 'reward_days')
            if reward_days > 0:
                entry['type'] = 'days'
                entry['reward_days'] = reward_days
                rewards.append(entry)

        if decision.get('stop'):
            break

    return rewards


def apply_referral_reward_policies(
    reward: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Applies referral reward policies to the calculation of one level."""
    result = {
        'reward_cents': _normalize_amount(reward.get('reward_cents', 0), 'reward_cents'),
        'reward_days': _normalize_amount(reward.get('reward_days', 0), 'reward_days'),
        'reward_policies': [],
    }

    for name, policy in list(REFERRAL_REWARD_POLICIES.items()):
        policy_context = dict(context)
        policy_context['reward'] = {
            'reward_cents': result['reward_cents'],
            'reward_days': result['reward_days'],
        }
        try:
            raw_decision = policy(policy_context)
            if inspect.isawaitable(raw_decision):
                raise ValueError('referral reward policy должна быть синхронной')
            decision = _normalize_referral_reward_decision(raw_decision)
        except Exception as e:
            logger.warning("Referral reward policy '%s' пропущена из-за ошибки: %s", name, e)
            continue

        if not decision:
            continue

        entry = _policy_entry(name, decision)
        if decision.get('ok') is False:
            result['reward_cents'] = 0
            result['reward_days'] = 0
            result['reward_policy'] = name
            result['reward_policies'].append(entry)
            break

        if 'reward_cents' in decision:
            result['reward_cents'] = _normalize_amount(decision['reward_cents'], 'reward_cents')
            result['reward_days'] = 0
            entry['reward_cents'] = result['reward_cents']
        elif 'reward_days' in decision:
            result['reward_days'] = _normalize_amount(decision['reward_days'], 'reward_days')
            result['reward_cents'] = 0
            entry['reward_days'] = result['reward_days']

        result['reward_policy'] = name
        result['reward_policies'].append(entry)

        if decision.get('stop'):
            break

    return result


def _register_policy(registry: dict[str, Callable], name: str, func: Callable, *, replace: bool) -> None:
    policy_name = _normalize_policy_name(name)
    _require_bool_option(replace, 'replace')
    if not callable(func):
        raise ValueError('policy должна быть callable')
    if policy_name in registry and not replace:
        raise ValueError(f"policy '{policy_name}' уже зарегистрирована")
    registry[policy_name] = func


def _normalize_policy_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("policy name должен быть строкой")
    value = name.strip().casefold()
    if not _POLICY_NAME_RE.fullmatch(value):
        raise ValueError("policy name должен соответствовать ^[a-z][a-z0-9_.:-]{0,127}$")
    return value


def _require_bool_option(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{field} должен быть bool')
    return value


def _normalize_pricing_decision(raw_decision: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw_decision is None:
        return {}
    if not isinstance(raw_decision, Mapping):
        raise ValueError('pricing policy должна вернуть dict или None')
    decision = dict(raw_decision)
    unknown = set(decision.keys()) - _ALLOWED_PRICING_KEYS
    if unknown:
        raise ValueError(f"неподдерживаемые поля решения: {', '.join(sorted(unknown))}")
    if 'final_amount' in decision and 'discount_amount' in decision:
        raise ValueError('нельзя одновременно возвращать final_amount и discount_amount')
    _validate_common_decision_fields(decision)
    for field in ('final_amount', 'discount_amount'):
        if field in decision:
            decision[field] = _normalize_amount(decision[field], field)
    return decision


def _normalize_amount(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{field} должен быть целым числом')
    amount = value
    if amount < 0:
        raise ValueError(f'{field} не может быть отрицательным')
    return amount


def _validate_quote_amounts(quote: dict[str, Any]) -> None:
    for field in ('original_amount', 'final_amount', 'discount_amount'):
        if field in quote:
            quote[field] = _normalize_amount(quote[field], field)


def _validate_common_decision_fields(decision: Mapping[str, Any]) -> None:
    for field in ('ok', 'stop'):
        if field in decision and not isinstance(decision[field], bool):
            raise ValueError(f'{field} должен быть bool')
    for field in ('label', 'reason'):
        if field in decision and decision[field] is not None and not isinstance(decision[field], str):
            raise ValueError(f'{field} должен быть строкой')
    if 'metadata' in decision and not isinstance(decision['metadata'], Mapping):
        raise ValueError('metadata должна быть словарём')


def _policy_entry(name: str, decision: Mapping[str, Any]) -> dict[str, Any]:
    entry = {'name': name}
    for key in ('label', 'reason', 'metadata'):
        if key in decision and decision[key] is not None:
            entry[key] = decision[key]
    return entry


def _normalize_promo_reward_decision(raw_decision: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw_decision is None:
        return {}
    if not isinstance(raw_decision, Mapping):
        raise ValueError('promo reward policy должна вернуть dict или None')
    decision = dict(raw_decision)
    unknown = set(decision.keys()) - _ALLOWED_PROMO_REWARD_KEYS
    if unknown:
        raise ValueError(f"неподдерживаемые поля решения: {', '.join(sorted(unknown))}")
    _validate_common_decision_fields(decision)
    if 'reward_days' in decision:
        decision['reward_days'] = _normalize_amount(decision['reward_days'], 'reward_days')
    return decision


def _normalize_referral_reward_decision(raw_decision: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw_decision is None:
        return {}
    if not isinstance(raw_decision, Mapping):
        raise ValueError('referral reward policy должна вернуть dict или None')
    decision = dict(raw_decision)
    unknown = set(decision.keys()) - _ALLOWED_REFERRAL_REWARD_KEYS
    if unknown:
        raise ValueError(f"неподдерживаемые поля решения: {', '.join(sorted(unknown))}")
    if 'reward_cents' in decision and 'reward_days' in decision:
        raise ValueError('нельзя одновременно возвращать reward_cents и reward_days')
    _validate_common_decision_fields(decision)
    for field in ('reward_cents', 'reward_days'):
        if field in decision:
            decision[field] = _normalize_amount(decision[field], field)
    return decision


__all__ = [
    'PRICING_POLICIES',
    'PROMO_REWARD_POLICIES',
    'REFERRAL_REWARD_POLICIES',
    'apply_pricing_policies',
    'apply_promo_reward_policies',
    'apply_referral_reward_policies',
    'register_pricing_policy',
    'register_promo_reward_policy',
    'register_referral_reward_policy',
]
