"""Static administrator help; placeholder resolution stays in placeholders.py."""

from bot.utils.text import escape_html


_EXAMPLE_NOTE = (
    "Примеры вымышленные, со стандартным оформлением. "
    "У пользователя будут его данные; оформление может быть изменено.\n\n"
)
_COMPATIBILITY_NOTE = (
    "\n\nСтарые поддерживаемые названия продолжают работать в сохранённых текстах. "
    "Для новых текстов используйте формы выше."
)


def _example(description: str, template: str, result_html: str) -> str:
    """Pair a literal template with a trusted, static rendered example."""
    return f"• {description}\n<code>{escape_html(template)}</code>\n→ {result_html}"


_KEY_FIELD_EXAMPLES = (
    ("Номер ключа", "Номер: %key(field=id)%", "Номер: 42"),
    ("Имя ключа", "Ключ: %key(field=name)%", "Ключ: Домашний"),
    ("Индикатор состояния", "%key(field=status)%", "🟢"),
    ("Использованный трафик и лимит", "%key(field=traffic)%", "10.00 GB из 100.00 GB (10.0%)"),
    ("Дата окончания", "До: %key(field=expires_at)%", "До: 2030-01-03"),
    ("Название сервера", "Сервер: %key(field=server)%", "Сервер: Германия"),
    ("Название тарифа", "Тариф: %key(field=tariff)%", "Тариф: Месяц"),
    ("Лимит устройств", "Устройств: %key(field=device_limit)%", "Устройств: 3"),
    ("Число оставшихся дней", "Осталось дней: %key(field=days_left)%", "Осталось дней: 2"),
    (
        "Остаток срока в днях, часах и минутах",
        "Осталось времени: %key(field=time_left)%",
        "Осталось времени: 2 дн., 13 ч., 29 мин.",
    ),
)
_KEY_FIELDS_HELP = "\n\n".join(_example(*example) for example in _KEY_FIELD_EXAMPLES)
_KEY_TIME_NOTE = (
    "\n\nДата окончания — не остаток срока. Для ключа без срока дата и остаток времени "
    "показывают «Без срока», число дней пустое. После истечения остаток равен нулю. "
    "Время рассчитано при отправке: уже отправленное сообщение не обновляется."
)

_KEY_LINK_HELP = "\n\n".join((
    _example("Ссылка моноширинным текстом для копирования", "%key_copy%",
             "<code>https://vpn.example/sub/demo</code>"),
    _example("Обычная ссылка в тексте", "%key_link%", "https://vpn.example/sub/demo"),
    _example(
        "Ссылка внутри параметра URL-кнопки",
        "https://app.example/import?url=%key_link_url%",
        "<code>https://app.example/import?url=https%3A%2F%2Fvpn.example%2Fsub%2Fdemo</code>",
    ),
))

_COUPON_HELP = "\n\n".join((
    _example(
        "Готовый блок купона", "%payment_coupon%",
        "🎫 <b>Купон на следующую покупку</b>\n\nСкидка: <b>15%</b>\n<pre>GIFT15</pre>",
    ),
    _example("Код купона", "Код: %payment_coupon(field=code)%", "Код: GIFT15"),
    _example("Скидка числом; знак % добавьте в текст",
             "Скидка: %payment_coupon(field=discount_percent)%%", "Скидка: 15%"),
    _example("Срок действия купона в днях",
             "Действует %payment_coupon(field=lifetime_days)% дней", "Действует 7 дней"),
))

EDITOR_PLACEHOLDER_HELP = {
    'main': (
        "📝 <b>Справка: Текст главной страницы</b>\n\n"
        "Вернитесь в редактор и отправьте новый текст или фото/видео/GIF с подписью.\n\n"
        + _EXAMPLE_NOTE
        + _example("Список тарифов с ценами, без заголовка", "%tariffs%",
                   "• Месяц — 199 ₽\n• Год — 1990 ₽")
        + "\n\n"
        + _example("Тарифы одной группы (1 — номер нужной группы)", "%tariffs(group_id=1)%",
                   "• Месяц — 199 ₽")
        + "\n\nЕсли подходящих тарифов нет, список пустой. Цены зависят от настройки валют. "
        "Чтобы не показывать список в тексте, не вставляйте эти подстановки.\n\n"
        "Совместимость: <code>%no_tariffs%</code> и его старое русское имя "
        "заменяются пустой строкой; они не скрывают тарифы или кнопки."
        + _COMPATIBILITY_NOTE
    ),
    'key_delivery': (
        "📝 <b>Справка: Текст выдачи ключа</b>\n\n"
        "Редактор принимает только текст.\n\n"
        + _EXAMPLE_NOTE
        + "<b>Ссылка подписки</b>\n" + _KEY_LINK_HELP
        + "\n\nURL-форма кодируется именно в адресе кнопки; в тексте сообщения "
        "она показывает обычный адрес.\n\n<b>Данные текущего ключа</b>\n"
        + _KEY_FIELDS_HELP + _KEY_TIME_NOTE
        + "\n\n<b>Купон за текущую оплату</b>\n" + _COUPON_HELP
        + "\n\nБез связанной оплаты с выданным купоном его подстановки пустые. "
        "Повторный показ не выдаёт новый купон. Без данных ключа его подстановки пустые."
        + _COMPATIBILITY_NOTE
    ),
    'notification_text': (
        "📝 <b>Справка: Текст уведомления об истечении</b>\n\n"
        + _EXAMPLE_NOTE
        + "<b>Данные ключа, о котором напоминает бот</b>\n"
        + _KEY_FIELDS_HELP + _KEY_TIME_NOTE
        + "\n\nЧисло дней берётся на момент проверки уведомлений; подробный остаток "
        "времени — на момент подготовки сообщения. Без данных ключа подстановки пустые. "
        "Русского аналога у <code>%key(field=time_left)%</code> нет."
        + _COMPATIBILITY_NOTE
    ),
    'referral': (
        "📝 <b>Справка: Реферальная страница</b>\n\n"
        + _EXAMPLE_NOTE
        + _example("Личная ссылка пользователя", "Приглашайте друзей: %referral_link%",
                   "Приглашайте друзей: https://t.me/ExampleBot?start=ref_demo")
        + "\n\n"
        + _example(
            "Личная ссылка внутри параметра URL-кнопки",
            "https://app.example/share?url=%referral_link_url%",
            "<code>https://app.example/share?url=https%3A%2F%2Ft.me%2FExampleBot%3Fstart%3Dref_demo</code>",
        )
        + "\n\n"
        + _example("Статистика включённых уровней; баланс при денежном вознаграждении",
                   "%referral_stats%", "✅ Уровень 1 (10%): 3 чел. — 60 ₽\n\n💰 <b>Ваш баланс:</b> 150 ₽")
        + "\n\nПри вознаграждении днями будет, например, «3 дн.» вместо суммы. "
        "Реферальная программа должна быть включена, а пользователь — известен боту. "
        "Без этих данных подстановки пустые. URL-форма кодируется в адресе кнопки, "
        "а в тексте показывает обычную ссылку."
        + _COMPATIBILITY_NOTE
    ),
}

BROADCAST_PLACEHOLDER_HINT = (
    "<b>Подстановки в тексте и подписи</b>\n"
    "<code>%user_name%</code> — имя получателя; "
    "<code>%referral_link%</code> — его личная ссылка.\n\n"
    + _example(
        "Вымышленный пример",
        "Привет, %user_name%! Ваша ссылка: %referral_link%",
        "Привет, Анна! Ваша ссылка: https://t.me/ExampleBot?start=ref_demo",
    )
    + "\n\nВ превью используются ваши данные, при отправке — данные каждого получателя. "
    "Для личной ссылки нужна включённая реферальная программа. "
    "Обычная рассылка не выбирает ключ или платёж: их данные без контекста пустые. "
    "В опросах подстановки не работают."
)
