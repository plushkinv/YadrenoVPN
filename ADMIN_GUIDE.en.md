# Bot administrator guide

This document covers only the actions available through the Yadreno VPN Telegram bot's admin panel.

Advanced changes through the built-in YaAdmin / Yadreno Admin agent, the `/yaa` and `/yaan` commands, additional settings, custom pages and detailed button configuration are covered separately in [YAADMIN_GUIDE.en.md](YAADMIN_GUIDE.en.md).

## Installation language

The language is chosen once during installation and applies to the bot, admin panel, website, Mini App and editor panel. Russian is the default. Telegram and browser language preferences do not change it; there is no language switch in settings. Updates preserve the chosen language.

This guide shows Russian button names with English explanations. Other languages use the same layout and actions. Saved names, messages, broadcasts and administrator edits are not translated automatically. Resetting customization restores stock texts in the installation language. Restoring a backup restores that backup's language and texts.

## Signing in to the admin panel

Administrators see the **⚙️ Админ-панель (Admin panel)** button on the bot's home page.

The admin panel provides:

- a summary of the bot, panels and nodes;
- server management;
- user and key management;
- plans, plan groups and trial subscriptions;
- payment methods;
- broadcasts and notifications;
- promo codes, promotional links and coupons;
- bot settings, text and links;
- backups, updates, logs and extension diagnostics;
- referral program settings;
- access to **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)** to change the bot's interface;
- access to **🤖 Yadreno Admin** for advanced tasks.

## Admin dashboard

The admin dashboard shows a brief operational summary:

- the status of VPN panels and nodes;
- important issues, if any;
- payments over the past day;
- user and key counts;
- active keys and keys nearing expiry.

**🔄 Обновить (Refresh)** rebuilds the summary. If a panel or node is unavailable, the issue appears on the same screen so the administrator can see what needs attention.

The dashboard, server list and server card first display their buttons and saved data, with **⏳ Загружается (Loading)** in place of network statistics. Once the request finishes, the statistics appear in the same message. You can navigate elsewhere while the panel responds; a late response will not bring back the previous screen. Data refreshes when you open the screen or press **🔄 Обновить (Refresh)**.

Each time you open the main admin menu, the bot first removes the regular keyboard: a temporary **⏳** message appears for about a second and disappears before the admin panel is shown.

## Servers and nodes

In **Серверы (Servers)**, administrators can:

- add a VPN server;
- view the server list and status;
- open a server card;
- change panel connection details;
- enable or disable a server for key issuance;
- delete an unused server;
- view node status if the panel supports it.

The server list summarizes panel and node availability, load, traffic and warnings. If there are many nodes, it shows problematic nodes first, then some healthy ones, and collapses the remainder into a short summary. A server card shows the selected panel's settings and statistics.

If the same physical panel is added several times with the same URL, monitoring shows it once, even with different API keys or virtual inbound groups. The dashboard also counts its metrics and nodes once. Buttons for opening and configuring each added entry remain separate.

When adding a server, first choose a connection method:

- **🔑 По API-ключу (Using an API key)** — recommended for official 3X-UI version 3.3.0 or later. Create a dedicated API token in the panel's security settings and copy it immediately. Panel two-factor authentication can remain enabled.
- **👤 Логин и пароль (Username and password)** — the alternative for the same official 3X-UI 3.3.0+. The bot signs in only to obtain its own API token, then uses that token for subsequent operations. Saved credentials also allow one attempt to recover a rejected token. Automatic sign-in does not work with two-factor authentication enabled; use an existing API key instead.

Both methods, and subsequent address or credential changes, are saved only after a successful connection check. Versions below 3.3.0, an unknown version or an unavailable modern API prevent saving. The administrator sees **«Не удалось подключиться к панели» (Could not connect to the panel)**; the detailed reason remains in the log. An unverified connection cannot be saved. The server card shows the verified version and check time.

If the connection uses only an existing API key and the panel later rejects it, the bot retains the key and checks it again on the next request. After a temporary failure, operation resumes when the panel accepts the key again. All administrators receive **«Панель не принимает API-ключ» (The panel is rejecting the API key)**. For one panel, subsequent failures trigger this message at most once per hour, including across bot restarts. It lists only servers where the bot has already encountered the error.

If the error persists, check the API key in 3X-UI and replace it in the listed server cards under **Серверы (Servers)** if necessary. Without saved login credentials, the bot cannot issue a new key itself. If it reports **«API-ключ панели не указан» (Panel API key is missing)**, save an API key through the server card.

### Virtual inbound groups

One physical 3X-UI panel can be added as several virtual servers, each with its own inbound set. In the add-server wizard, after the URL, enter only the group number without hyphens, such as `1` or `2`. This field is optional: **⏭ Пропустить (Skip)** keeps the ordinary mode, where the server uses all eligible inbounds. You can change the group in the server card or clear it with **🌐 Все inbound’ы (All inbounds)**.

Configure the group in the panel under:
**3X-UI → Inbounds → edit the connection → «Расширенный шаблон» (Advanced template) → «Всё» (All) → the `"tag"` field**.

Modify only the end of the existing `tag`, adding `--1`, `--2`, `--3` and so on. For example:

```json
"tag": "in-443-tcp--1"
```

An inbound can belong to several virtual servers. Append a continuous sequence of markers: `in-443-tcp--1--2` belongs to groups 1 and 2. When adding, removing or moving membership, preserve the original `tag` prefix and other group numbers; replace only the relevant marker. Do not repeat markers, and check that all final panel `tag` values remain unique before saving.

Do not use a single-hyphen suffix such as `-1`: it can overlap with automatically generated 3X-UI tags and is not recognized as a group. Technical `tag` markers are not displayed in connection names shown to users.

Excluding an inbound from user subscriptions is separate: put `--!` at the beginning of **Remark**, not in `tag`. If Remark starts with `--!` after leading spaces, the inbound is excluded regardless of its group markers. The bot detaches existing clients during the next successful full synchronization, usually within 30 minutes. After removing `--!`, an enabled inbound in the appropriate group is added back to active keys.

Disabling an inbound normally in 3X-UI preserves client bindings: the panel itself stops including it in subscriptions. Renewals and limit changes continue to apply, so customers retain current expiry dates and limits when it is enabled again. The bot does not enable an inbound or its node itself. Customers must refresh their subscription in their VPN app to see the new connection set.

The connection check verifies the panel and API, but does not require an inbound for the specified group to exist yet. An empty group can be saved, but key issuance will not complete until the `tag` configuration is corrected.

### MTProto in the shared subscription

On 3X-UI version 3.5.0 or later, MTProto can join the same shared subscription as ordinary VPN protocols. No separate plan or issuance is needed: the user keeps the existing subscription URL, and the panel adds `tg://proxy` with an individual key.

Create and enable an MTProto inbound in 3X-UI. The bot attaches existing subscriptions during the next synchronization. On supported versions 3.3.x–3.4.x, MTProto is outside the target topology; from 3.5.0 onward it is included automatically. To exclude an inbound from subscriptions, add the standard `--!` prefix to its name.

## IP address or device limits

Under **Настройки бота (Bot settings)** → **📱 Ограничение устройств (Device limits)**, choose one common mode for all keys:

- **IP-адреса (IP addresses)** — the standard mode, supported by all compatible panels;
- **Устройства (Devices)** — limits registered devices and requires 3X-UI version 3.7.0 or later.

When enabling device mode, the bot checks all active main panels. If any panel is unavailable, has an unknown version or has not been upgraded to the required version, the mode remains unchanged and the problematic panels are listed. Main-panel nodes are not checked separately. You can always switch back to IP limits.

The **Устройств (Devices)** value in a plan remains the single limit. IP mode sends it to the panel as an IP address count; device mode sends it as an HWID count. Switching does not immediately update every key: existing keys receive the new pair of limits during the next regular synchronization, usually within 30 minutes. New keys use the selected mode immediately.

In device mode, the user's configured key card includes **📱 Мои устройства (My devices)**. The list is fetched directly from the panel and shows model, OS, app, first seen and last seen. Individual devices can be removed immediately without confirmation. Opening the subscription from that device again may register it again; this is normal. There is no remove-all-devices button.

## Users and keys

In **Пользователи (Users)**, search for a customer by Telegram ID, username or key details.

Open **📋 Все пользователи (All users)** to browse the list and configure filters. Each filter is independent: `🟢` means selected and `⚪` means off. Multiple filters are combined with **AND**: for example, **«С активными ключами» (With active keys)** and **«Пробный период» (Trial period)** show users who have ever activated a trial and currently have an unexpired key. Without filters, everyone is shown, including banned users.

Available filters:

- **С активными ключами (With active keys)** — has a key with an unexpired or unlimited validity period.
- **Без ключей (Without keys)** — has no keys at all; a key awaiting setup still counts as a key.
- **Ключ истёк (Key expired)** — has an expired key and no key with a current validity period.
- **Никогда не покупали (Never purchased)** — has no successful paid key purchase or renewal. Trials, entirely free promotions, demos and balance top-ups do not count as purchases. Deleting a purchased key does not erase purchase history.
- **Пробный период (Trial period)** — has activated a trial at least once.
- **В боте (In the bot)** — Telegram is linked; **На сайте (On the website)** — phone/password login is configured. Selecting both shows users with both logins. These two filters and statistics appear only while the website is enabled.
- **Забаненные (Banned)** — blocked by an administrator.
- **Заблокировали бота (Blocked the bot)** — blocked the bot themselves in Telegram.

Filters appear in two columns on the first page only; the list updates immediately after a click. On later pages, **🔎 Изменить фильтр (Change filters)** returns to the first page and clears the entire selection. Page arrows and returning from a user card preserve filters. If no user matches, an empty list appears and the selected filters remain available to change. If the website is disabled while browsing, the next action clears only **«В боте» (In the bot)** and **«На сайте» (On the website)**, retains the other filters, and opens the first page.

In the general list, each button first shows `@username` or Telegram ID, followed after `=` by the user's first and last name if saved in their profile.

An account without linked Telegram appears as **«🌐 Аккаунт #…» (Account #…)**. Its card indicates that Telegram is absent; private messaging is unavailable.

Such accounts still support keys, manual issuance and renewal, plan changes, blocking, balance adjustments and referral multipliers. Correspondence is available through a connected web support module: replies are saved in the ticket history. Linking Telegram later does not send old web messages there.

A user card provides:

- user profile and status;
- the user's keys;
- manual key issuance;
- key renewal;
- plan changes;
- used-traffic resets;
- key deletion;
- blocking and unblocking;
- manual balance credits or debits;
- an individual referral multiplier;
- private messages sent on behalf of the bot.

For manual issuance, select a group and an available server in that group, then set traffic, validity and device count. There is no protocol selection: the bot applies the logical key to every eligible inbound on the selected server. Traffic `0` means unlimited, and `0` days means no expiry.

In an active key's card, the first button, **📋 Показать подписку (Show subscription)**, opens the same QR code, copyable link and instructions that the user sees. It is available when the subscription is configured, unexpired and has traffic remaining. **⬅️ Назад (Back)** returns to that key's card; **🈴 На главную (Home)** opens the admin menu.

For a ready key, **📋 Изменить тарифный план (Change plan)** completely reissues its terms: it assigns a new plan, validity period, traffic allowance and device count from the current time, and resets used traffic. Choose any ordinary plan in the current group, including hidden plans, or **🛠 Произвольный тариф (Custom plan)** to enter individual values. The bot asks for confirmation before applying the change. This differs from an ordinary paid renewal, which accumulates validity and the final traffic allowance.

**📅 Продлить (Renew)** changes only the key's validity, including **«Без срока» (No expiry)** when you enter `0`. Used traffic, the current limit and the VPN panel counter are not reset. To reset usage separately, use **🔄 Сбросить трафик (Reset traffic)**.

The key card shows its current plan and effective device count, including an individual value for a custom plan.

The upper traffic limit of an existing key cannot be changed separately. To start a new allowance, change the plan; to reset only usage, use **🔄 Сбросить трафик (Reset traffic)**.

If a user has blocked the bot in Telegram, the admin panel shows that status. This alone does not disable VPN access: access changes only through administrator actions on the user or their keys.

### User messages and support

On a user card, **💬 Написать (Write a message)** opens a quick conversation with the customer.

An administrator can send text or a file of any format in one message, such as a PDF, text document or archive. Photos, videos, GIFs, audio, voice messages, video messages, stickers and Live Photos are also supported. Captions and formatting are preserved. Users have the same options when opening a support request or replying.

If a contact, location or poll is sent instead of supported content, the bot asks for a suitable message. You can retry immediately.

The user receives a bot message with **💬 Ответить (Reply)**. Their reply goes to the administrator who started the conversation.

Users can also initiate support if the home page has **💬 Написать в поддержку (Contact support)** enabled or by using `/support`. User-initiated requests go to all administrators. The first administrator to reply takes ownership; later user replies go only to that administrator.

The home-page **💬 Написать в поддержку (Contact support)** button is hidden by default. Enable it through home-page button customization in YaAdmin / Yadreno Admin.

The screen asking users to enter a support request and the support status messages can be edited through YaAdmin using `/yaa` directly on the open screen. Back/cancel buttons and support correspondence work automatically.

The request card delivered to the administrator after the user's message can be customized through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**: change its text, media and buttons, or add actions tailored to the particular request. Describe the action and the conditions for showing it. The user's original message or file arrives separately; card changes apply to new support messages.

## Plans

In **Тарифы (Plans)**, administrators can:

- create a plan;
- set its name, duration, price, traffic allowance and device count;
- select its group;
- enable or hide it;
- change an existing plan's parameters.

A hidden plan is not offered to buyers, but an administrator can use it when fully reissuing a key in the same group.

A duration of `0` means **Без срока (No expiry)**, and traffic `0` means **Безлимит (Unlimited)**. These are saved and sent to the VPN panel as true unlimited values; the bot does not substitute an arbitrary 30 days or a large number of gigabytes.

Each group has an internal **Произвольный тариф (Custom plan)**. Buyers do not see it, it cannot be selected for a trial, and it is not editable in the general plan list. Administrators use it only for manual issuance or reissuance. Users can later renew such keys using an ordinary active plan in the same group.

A plan has one price in the bot's common base currency: RUB by default, with an option to switch to USD. Separate manually entered prices per currency are not used. The bot calculates RUB, USDT, Telegram Stars and connected payment-method currencies using coefficients from **Оплаты (Payments)**. The plan card shows calculated prices in all currencies currently available for payment, just like the plan list on the home page. A currency is not repeated for multiple providers. Prices omit unnecessary zeros, such as `1` instead of `1.0` and `1.5` instead of `1.50`.

## Plan and server groups

Groups separate plans and servers, for example into basic and premium access.

Administrators can:

- create a group;
- assign a plan to a group;
- assign a server to a group;
- control which servers are available for a plan;
- independently enable or disable **Автосброс трафика 1-го числа (Automatic traffic reset on the 1st)** for each group.

If a paid plan has one available server, the bot can issue or configure its key automatically. If there are several servers, the user selects one in the bot.

Automatic reset is no longer a bot-wide setting. In a group with first-of-month resets enabled, active keys receive their current plan's allowance again, while usage and notification thresholds are reset. A custom key receives its saved individual allowance. Unlimited keys remain unlimited, but their usage counters also restart. Groups without automatic resets continue accumulating purchased allowances as before.

You can therefore offer accumulating traffic plans and monthly allowance plans simultaneously in different groups. Enabling automatic reset for an accumulating group really does replace its unused remainder with the current plan's allowance on the first day of the month.

### Linking to a main subscription

The group card includes **🔗 Привязка к подписке (Link to a subscription)**. For example, sell an unlimited monthly key in one group and a separate traffic allowance key in another, then include the second key's servers in the first key's subscription.

This setting belongs to the additional key's group. Select the group in which the bot should look for a subscription to link to, or **Не привязывать (Do not link)**. After an ordinary purchase and issuance of the additional key, the bot checks the same user's eligible active keys in the selected group:

- if none are eligible, the purchase still completes and the new key remains separate;
- if exactly one is eligible, the bot links the new subscription automatically;
- if several are eligible, the user selects one after issuance or presses **Продолжить отдельно (Continue separately)**.

If key setup completes through background recovery without a current user screen, the bot does not choose among several eligible keys itself; the new key remains separate.

The Mini App uses the same group setting. **Основная подписка (Main subscription)** selection appears after a completed purchase, trial issuance or replacement of an unlinked key. With several options, choose **Привязать (Link)** or **Оставить отдельно (Keep separate)**. The ordinary subscription card has no separate merge button. Linking adds servers to the shared URL but does not combine the two keys' validity periods or limits.

The standard selection screen shows at most the first 50 eligible keys and explicitly tells the user. This is only a safe Telegram interface boundary, not a limit on key or relationship counts. Search or pagination for larger lists can be added through the Customizer.

An additional key is not hidden or converted into a special package. It remains visible in **«Моих ключах» (My keys)**, has its own URL and QR code, and is purchased, renewed, replaced and deleted separately. No per-group key-count restrictions are introduced.

Linking applies only to new issuance and replacement of a key that is not yet linked. If a user first buys the additional key and later buys an eligible key in the selected group, the bot does not search for older keys automatically. Changing the lookup group or disabling linking also leaves existing links unchanged. Use the Customizer for a tailored workflow.

You can form chains across several groups, but cannot choose the same group or create a cycle; the bot rejects that configuration. The target key must be on a server running official 3X-UI 3.4.0 or later. Servers on 3.3.x remain supported for ordinary keys, but cannot receive linked subscriptions in this workflow.

## Trial subscription

The standard **Пробная подписка (Trial subscription)** section controls the main trial offer. You can:

- enable or disable the main offer;
- select an ordinary active or hidden plan;
- edit the shared trial confirmation page.

The standard home-page button shows only the main offer. It appears when the offer is enabled, a plan is selected, and the user is still eligible. The group's internal **Произвольный тариф (Custom plan)** cannot be selected.

Through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**, create additional trial offers using other ordinary plans and add dedicated buttons. Place several direct buttons on a page or create an informational page listing the offers. There is no ready-made system screen that automatically lists every trial; its contents and presentation are chosen by the administrator.

All new offers use one of two modes:

- **Один пробник на пользователя (One trial per user)** — the first activation makes every other offer unavailable. This is the default.
- **Один пробник в каждой группе тарифов (One trial per plan group)** — the user can activate only one offer within a group, but an offer in another group remains available.

The mode is a hidden setting changed through Yadreno Admin. Switching to group mode makes unused groups available; switching back to per-user mode again considers any previous activation. Users who took a trial before the update remain ineligible in every group.

All offers share one editable confirmation page. A plan with `0` days or `0` GB issues a true **Без срока (No expiry)** or **Безлимит (Unlimited)** key. If VPN-panel setup temporarily fails after confirmation, the created key remains in **Моих ключах (My keys)** for another setup attempt; a second trial is not issued.

## Payments

In **Оплаты (Payments)**, administrators manage available payment methods.

Supported methods:

- **Telegram Stars**;
- **Crypto Pay through @CryptoBot**;
- **USDT through Ya.Seller**;
- **TG Payments**;
- **ЮКасса (YooKassa)**;
- **WATA**;
- **Platega**;
- **Cardlink**;
- **NOWPayments, Stripe, PayPal, PayTabs and MyFatoorah**;
- **user balance**;
- **demo payment**, when needed to check workflows.

For each method, the admin panel provides the relevant controls: enable or disable payments, enter connection details from the provider account, and check current status.

### Method visibility in administration

Russian installations initially hide the five new providers and show direct
YooKassa. Other installation languages show the new providers and hide direct
YooKassa. Ya.Seller is initially hidden in every language; other methods, including
TG Payments, remain visible. Ask the Customizer or Yadreno Admin, for example:
“Show Stripe in the administrator's Payments section.”

Hiding a card does not disable payments, hide an already connected method from
customers or prevent checking existing invoices. Use the method's enable switch
to stop new payments. A full customization reset restores visibility for the
installation language while keeping connection details and enablement.

### NOWPayments, Stripe, PayPal, PayTabs and MyFatoorah

Open the provider card in **Payments**, fill in its fields, set the required rate
in **Currency and rates**, then enable payments. New methods start disabled.

| Provider | Connection details | Invoice currency |
| --- | --- | --- |
| NOWPayments | API key, account email and password | USD |
| Stripe | Secret API key | USD |
| PayPal | Client ID, Client Secret and Live or Sandbox environment | USD |
| PayTabs | Profile ID, Server Key and account region | USD |
| MyFatoorah | API token, region and matching account currency | Account currency |

These integrations need no project domain or incoming notifications: the bot
checks payments through the API. NOWPayments refreshes authentication using the
account email/password. After PayPal approval, the bot additionally captures the
payment. PayTabs displays its own result page; the buyer returns to the bot
manually. Partial payments do not complete an order, and repeated checks do not
credit it again. Verify the connection using credentials for the selected
provider environment.

MyFatoorah supports AED, SAR, QAR, EGP, BHD, KWD, OMR and JOD. Selecting a region
fills its currency; check it against the account. BHD, KWD, OMR and JOD use three
decimal places, the others two. Enter the fixed rate in the direction shown by
the bot. A new rate affects new invoices only. Installation language never changes
the RUB/USD base currency by itself.

### TG Payments

TG Payments connects a payment provider through Telegram. Obtain the selected
provider's token through BotFather, save it in **TG Payments**, and choose RUB or
USD as supported by that connection. Enable the separate receipt option for
YooKassa with RUB; it can be disabled for other connections and does not apply to
USD. Direct YooKassa is configured separately. Updates preserve previous settings
and invoices; changing currency or receipt mode affects new invoices.

### Crypto Pay through @CryptoBot

Crypto Pay is configured separately from Ya.Seller:

1. Open @CryptoBot → **Crypto Pay** → **Create App** and obtain the app API token.
2. In your bot, open **Оплаты (Payments)** → **Crypto Pay** → **Изменить API-токен (Change API token)** and send the token. The bot deletes the token message, checks it through Crypto Pay, and displays only a masked value.
3. Press **Включено (Enabled)**. The token is checked again before enabling.

Invoices use the current RUB or USD base currency and last 60 minutes. There is no need to select coins or enter their exchange rates: Crypto Pay offers all supported coins and converts the amount itself. Do not configure a webhook; the bot checks invoices only through the Crypto Pay API.

Automatic checks occur 2, 5, 10, 20 and 30 minutes after creation. There is no separate check at minute 60. Between minute 30 and expiry, the user can press **✅ Я оплатил (I have paid)** on the ordinary invoice screen.

Disabling Crypto Pay prevents new invoices but retains the token and allows checking existing invoices. Before replacing the token, the bot checks old invoices with the old token: it completes paid invoices, closes expired ones, and prevents replacement while active invoices remain or their status cannot be established reliably.

Ya.Seller remains functional. Its card is hidden by default regardless of enablement;
ask the Customizer or Yadreno Admin to show it when you need to change its connection.

To connect Platega, contact **@platega_connect_manager** and **make sure to mention promo code `PLUSHKIN`**: it gives a **1% fee reduction for each payment method**. A ready-to-send Russian message is: «Хочу подключить Platega по промокоду PLUSHKIN» (I would like to connect Platega using promo code PLUSHKIN).

After connecting, enter the **Merchant ID** and **API key** from your account. Leave **Callback URL** in the Platega panel empty: the bot checks payment status itself.

**💱 Валюта и курсы (Currency and rates)** shows the current base currency and fixed conversion coefficients. Each coefficient means `1 base currency = X payment currency`. For example, with a USD base, set the RUB, USDT or Stars amount corresponding to `1 USD`. These coefficients serve the relevant payment methods and price previews. Crypto Pay receives the base RUB/USD amount directly and needs no per-coin rate.

RUB is the default base currency. To switch to USD:

1. Press **💵 Сменить базовую валюту на USD (Change base currency to USD)**.
2. Enter the transition rate in the displayed direction, for example the number `90` for `1 USD = 90 RUB`.
3. Review the preview of plan prices, total user balances, referral earnings and the number of unpaid invoices to cancel.
4. Press **✅ Переключить на USD (Switch to USD)**.

Before changing the currency, the bot creates and verifies a database backup. Plan prices, current user balances and current referral earnings are recalculated automatically. Other currency coefficients are carried over so that switching the base alone does not immediately change RUB, USDT or Stars invoice amounts. You can edit the coefficients afterward.

Unpaid invoices are canceled and their reserved promo codes released. Active Crypto Pay invoices are first checked and deleted through the provider; if payment is detected, ordinary issuance or crediting completes first. If status or invoice deletion cannot be confirmed, the currency stays unchanged. If another provider has already confirmed payment or the bot is issuing access, switching is temporarily unavailable; wait for processing to finish. Historical paid operations are not recalculated and retain their original currency in reports.

To change an ordinary coefficient, enter a positive number with a decimal point or comma. The new coefficient applies only to new invoices; existing invoices retain their original calculation. Automatic online rate updates do not change invoice amounts.

When payment notifications are enabled, administrators receive a message for every successful purchase, renewal or user top-up. It includes the user, plan or credited amount, actual amount paid, payment method, applied promo code or coupon, and key operation details.

## User balance

Balance can be used for purchases inside the bot when that method is enabled.

Administrators can:

- manually credit a user's balance;
- debit it;
- view it on the user card;
- allow key purchases using balance.

If a user pays part of the price with balance, the remainder can be paid through another available method when that workflow is enabled.

Self-service top-ups are not part of the standard menu. They can be added through YaAdmin / Yadreno Admin customization. The user specifies the amount to credit and selects a payment method; the bot creates and checks the invoice. Balance cannot be used to top up itself. If a promo code reduces the payment, the user still receives the full amount they requested to credit.

## Broadcasts and notifications

Under **Маркетинг (Marketing)** → **Рассылка (Broadcast)**, prepare content for users:

- write text;
- add a photo with a caption;
- send or forward a native Telegram poll;
- preview the content;
- select recipients;
- start sending.

Text and photo captions support page placeholders. For example:

```text
Hello, %user_name%! Invite friends using your link: %referral_link%
```

Example output with fictional data:

```text
Hello, Anna! Invite friends using your link: https://t.me/ExampleBot?start=ref_demo
```

Each recipient sees their own name and referral link. The referral program must be enabled. Both the ordinary preview and the `/yaa` editor preview show data for the administrator opening them.

Values depend on the message context: an ordinary broadcast is not associated with a particular key or payment, so placeholders requiring that data are empty. The bot validates placeholder names and parameters when saving. Poll text is shared by all participants.

Recipient conditions are independent. Enable several to combine them with **AND**. With none selected, the broadcast targets all available users. Conditions cover users with active keys, without active keys, who never purchased, with expired keys, and who took a trial. **«Никогда не покупали» (Never purchased)** means no successful paid purchase or renewal, so combine it with **«Брали пробный период» (Took a trial)** to target trial users who have not subsequently paid.

Recipient count updates after each toggle. If nobody matches, the screen states that the broadcast cannot start and provides no send-confirmation buttons.

On the same screen, send `/yaa` and describe the desired text, photo caption, poll, style and recipient conditions. Follow-up instructions need no repeated command. Changes are saved in a separate draft and do not affect the working broadcast until you press **💾 Сохранить (Save)** or **🚀 К запуску (Prepare to send)**. To start with voice, send `/yaa` without text, then send a voice message, audio recording or text after the invitation. Voice can also continue an existing editor conversation.

**🚀 К запуску (Prepare to send)** displays a real Telegram preview but sends nothing yet. Sending starts only after the second button, **✅ Разослать N (Send to N)**. Changes to the text, selected conditions or recipient count require fresh confirmation.

Create a poll directly in the bot conversation or prepare it in Saved Messages, a group or a channel. Closed polls and forwarding-protected messages are rejected. Ordinary polls, multiple-answer polls and quizzes are supported.

If an imported poll has no votes, the bot automatically prepares a clean copy under its own identity. If votes already exist, you can restart counting without showing the source, or retain results with a visible source. All recipients participate in one shared poll.

The bot warns before sending a non-anonymous poll. For a clean poll, the final report includes **🛑 Закрыть опрос (Close poll)**. A poll with a retained source must be closed where it was created.

During sending, **🛑 Остановить рассылку (Stop broadcast)** stops the process gracefully: a send already in progress finishes, no new messages are sent, and the bot shows the final report.

The notifications section lets you configure key-expiry reminder text. The bot inserts current user and key data when sending.

Key-expiry and traffic-remaining reminders support all `%key(field=...)%` placeholders. For example, `Time remaining: %key(field=time_left)%` produces `Time remaining: 2 дн., 13 ч., 29 мин.` (2 days, 13 hours, 29 minutes with the default Russian unit labels), while `%key(field=days_left)%` gives complete days. The Customizer can change day, hour and minute labels. Older `%key_name%`, `%key_days_left%` and Russian aliases continue to work.

Common page placeholders, including `%referral_link%`, also work in key-expiry, traffic-remaining, new-referral and referral-purchase notifications. The personal link belongs to the notification recipient; the new referral or buyer name refers to the person being reported. Saved texts and older Russian placeholder names remain supported.

## Promo codes and coupons

**Маркетинг (Marketing)** offers two discount tools.

**Промокоды (Promo codes)** are reusable campaign and partner codes. Reuse applies across different customers: one user can successfully use the same code only once. Share a code as text or as a promotional link such as `https://t.me/<bot>?start=pr_<CODE>`. Following the link saves the code for the next purchase or renewal.

Under **Маркетинг (Marketing)** → **Промокоды (Promo codes)**, you can:

- add a code;
- set a discount from 0 to 100%;
- set an expiry date;
- set a total redemption limit across different users or leave it unlimited;
- enable or disable the code;
- open its card and copy the promotional link.

A 0% discount tracks visits and purchases without changing the price. A 100% discount makes a purchase or renewal free; no external invoice is created.

A code is used only after successful payment completion or successful free issuance with a 100% discount. Canceling an unpaid invoice does not consume the user's eligibility: its reservation is released and the code can be used again. After successful use, the code is cleared from the active payment and does not affect subsequent payments.

**Купоны (Coupons)** are single-use codes. Give one to a customer or partner, or use them in a channel giveaway. Users enter them in the same field as promo codes.

The promo-code/coupon input screen and the result of checking a code or promotional link can be edited through YaAdmin using `/yaa` on that screen. Back buttons and code verification work automatically.

The **🎟 Ввести промокод (Enter promo code)** button appears on purchase and renewal screens only if at least one available promo code or coupon exists: enabled, unexpired and below its redemption limit.

Under **Маркетинг (Marketing)** → **Купоны (Coupons)**, you can:

- open **Автовыдача при покупке (Automatic issuance after purchase)**;
- open **Автовыдача не продлившим ключ (Automatic issuance to users who did not renew)**;
- generate a batch of single-use coupons.

The main screen shows whether each scenario is enabled. Inside a scenario, select **Включено | Выключено (Enabled | Disabled)**. Each has all-time statistics — issued, redeemed, active, expired and conversion — plus its own discount and lifetime settings.

**Автовыдача при покупке (Automatic issuance after purchase)** runs after any successful paid purchase, renewal or internal balance top-up. It does not issue a coupon after free access with a 100% promo discount, a trial, demo payment or a zero-payment operation.

The post-payment coupon is displayed through `%payment_coupon%`. Its default block shows the code and discount but not its lifetime. To place the lifetime or an individual value yourself, use `%payment_coupon(field=code)%`, `%payment_coupon(field=discount_percent)%` or `%payment_coupon(field=lifetime_days)%`. For a new key purchase, the standard bot shows the block in the successful-payment message before key selection and creation; it is not repeated on service screens or key delivery by default. Through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**, remove it from the payment result, move it to key delivery, or use it on another page of the same payment workflow. To send the coupon as an extra message, configure the initially empty `payment_coupon_message` page and add `%payment_coupon%`; clearing its text and media disables that extra delivery again. Every presentation shows the same coupon, without creating another code.

**Автовыдача не продлившим ключ (Automatic issuance to users who did not renew)** is disabled by default. It sends a coupon to a customer whose keys have all expired and who has no active or perpetual key. Set a delay of 1–30 complete days, with 7 days as the default. The screen also shows how many deliveries are pending.

Enabling the scenario starts tracking only new customer departures. Historical expirations and periods while the feature was disabled do not become a delayed broadcast. Before sending, the bot rechecks the user's keys; if the customer has returned, delivery is canceled. A future departure can make them eligible for one coupon again.

Customize this message, its media and buttons through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**. By default it has separate **🔑 Мои ключи (My keys)** and **🈴 На главную (Home)** buttons.

The coupon generator asks for discount, lifetime and quantity in sequence, then returns all generated codes as one copyable block.

## Editing text

Under **Настройки бота (Bot settings)** → **✏️ Редактирование текстов (Edit text)**, change the main texts without restarting the bot.

The ordinary admin panel edits:

- home-page text;
- help text;
- pre-payment text;
- trial subscription text;
- the referral page;
- key-expiry notification text;
- key-delivery text;
- links and labels for **Новости (News)** and **Поддержка (Support)** buttons.

The editor shows a preview. To save a new version, send text or media with a caption in the chat. Use **🗑 Удалить медиа (Remove media)** to remove media.

Payment methods in the Mini App and website use the same names as the corresponding bot buttons. Through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**, rename these buttons, including extension payment methods. Purchase and renewal have separate labels; specify that both screens should match if needed. New names appear when plan selection next loads; there is no need to rename methods separately in the standard web interface.

### Placeholders: what to write and what the user sees

A placeholder is a variable replaced with data when the bot displays a page or sends a message. Use the English forms below. Examples use fictional data and default formatting: each user has their own names, amounts, dates and links, and formatting can be changed through the Customizer. Russian literals in generated output below illustrate the default interface; their meanings are explained in English.

The editor's help provides examples for the selected text. Placeholders also work in button labels, and link placeholders work in URL-button addresses when the required data is available. Ready-made multiline blocks are primarily intended for message text.

| Template | Purpose | Example output |
| --- | --- | --- |
| `Hello, %user_name%!` | Recipient's name | `Hello, Anna!` |
| `%user_username%` | Telegram username, if set | `@anna` |
| `ID: %telegram_id%` | Recipient's Telegram ID | `ID: 123456789` |
| `%bot_username%` | Bot username without @ | `ExampleBot` |
| `%page_key%` | Current page identifier | `main` |
| `Member since %user_registered_at%` | Registration date | `Member since 2026-09-01` |
| `Balance: %user_balance%` | Current balance | `Balance: 150 ₽` |
| `Total keys: %keys_total%` | Key count | `Total keys: 3` |
| `Active: %keys_active%` | Active keys | `Active: 2` |
| `Inactive: %keys_expired%` | Other keys | `Inactive: 1` |

**Plans and referrals.** `%tariffs%` displays a list without a heading, for example:

```text
• Месяц — 199 ₽
• Год — 1990 ₽
```

Here the saved plan names mean Month and Year. `%tariffs(group_id=1)%` lists only group 1, for example `• Месяц — 199 ₽`. Substitute the required group number. If no plans match, the list is empty; price formatting depends on configured currencies. To omit plans from the text, omit the placeholder; no separate marker is needed.

`Invite friends: %referral_link%` → `Invite friends: https://t.me/ExampleBot?start=ref_demo`.
`%referral_stats%` shows enabled levels and, for monetary rewards, the balance. Example for one level:

```text
✅ Уровень 1 (10%): 3 чел. — 60 ₽

💰 Ваш баланс: 150 ₽
```

This means Level 1 (10%): 3 people — ₽60; your balance: ₽150. With day-based rewards, an amount is replaced by a duration such as `3 дн.` (3 days). The referral program must be enabled and the user known to the bot. Without that data, the link and statistics are empty.

**Current key.** These values are available on the key card and delivery screen, in each key-list row, and in expiry and traffic-remaining notifications. An ordinary broadcast does not select a key automatically.

| Template | Purpose | Example output |
| --- | --- | --- |
| `Number: %key(field=id)%` | Key number | `Number: 42` |
| `Key: %key(field=name)%` | Key name | `Key: Home` |
| `%key(field=status)%` | Status indicator | `🟢` |
| `%key(field=traffic)%` | Used traffic and limit | `10.00 GB из 100.00 GB (10.0%)` — 10 GB out of 100 GB |
| `Until: %key(field=expires_at)%` | Expiry date | `Until: 2030-01-03` |
| `Server: %key(field=server)%` | Server name | `Server: Germany` |
| `Plan: %key(field=tariff)%` | Plan name | `Plan: Month` |
| `Devices: %key(field=device_limit)%` | Device limit | `Devices: 3` |
| `Days left: %key(field=days_left)%` | Remaining days | `Days left: 2` |
| `Time remaining: %key(field=time_left)%` | Days, hours and minutes | `Time remaining: 2 дн., 13 ч., 29 мин.` — 2 days, 13 hours, 29 minutes |

The expiry date is not the remaining duration. For a perpetual key, the date and remaining-time fields show `Без срока` (No expiry), while days left is empty. After expiry, remaining time is zero. In notifications, the day count is calculated at check time and detailed remaining time at message preparation. Sent text does not update itself.

`%keys_list%` shows the user's complete key list with each row's saved formatting. Example for one key:

```text
🔑 Домашний
🟢 · 10.00 GB из 100.00 GB (10.0%)
📅 До 2030-01-03
📍 Германия
```

This shows the Home key, 10 GB used out of 100 GB, valid until 2030-01-03, on the Germany server.

**Links and buttons.** On key delivery, `%key_copy%` shows `https://vpn.example/sub/demo` as copyable monospace text, while `%key_link%` shows the same address as a regular link. If Web/Mini App is enabled, ask the Customizer to add an **«Добавить в INCY» (Add to INCY)** button or one for another supported app. For INCY, the complete URL-button address is `%client_import_url(client=incy)%`. It opens a page that hands the subscription to the app; the browser may require another **«Открыть INCY» (Open INCY)** click. The generated link is private, just like the subscription. The button is hidden when Web is disabled or outside delivery of a specific subscription.

URL forms encode addresses when inserted **into a parameter of a button URL**:

| Button URL with placeholder | Example resolved URL |
| --- | --- |
| `https://app.example/import?url=%key_link_url%` | `https://app.example/import?url=https%3A%2F%2Fvpn.example%2Fsub%2Fdemo` |
| `https://app.example/share?url=%referral_link_url%` | `https://app.example/share?url=https%3A%2F%2Ft.me%2FExampleBot%3Fstart%3Dref_demo` |
| `https://app.example/pay?url=%payment_link_url%` | `https://app.example/pay?url=https%3A%2F%2Fpay.example%2Finvoice%2Fdemo` |

In ordinary page text, URL forms show the unencoded address. For a button opening the subscription directly, `%key_link%` → `https://vpn.example/sub/demo` is sufficient. Likewise, ordinary referral or payment links can serve as complete button URLs. `https://`, `http://` and `tg://` are allowed.

**Payments and coupons.** A related payment is required. Coupon values appear only after a coupon has already been issued for that payment; a placeholder does not issue a new one.

| Template | Purpose | Example output |
| --- | --- | --- |
| `%payment_provider%` | Payment method | `ЮKassa` |
| `%payment_tariff%` | Plan being paid for | `Месяц` (Month) |
| `Amount due: %payment_amount%` | Amount with currency | `Amount due: 199 ₽` |
| `%payment_link%` | Payment URL | `https://pay.example/invoice/demo` |
| `Code: %payment_coupon(field=code)%` | Issued coupon code | `Code: GIFT15` |
| `Discount: %payment_coupon(field=discount_percent)%%` | Discount; the template adds the % sign | `Discount: 15%` |
| `Valid for %payment_coupon(field=lifetime_days)% days` | Coupon lifetime | `Valid for 7 days` |

Instead of individual values, `%payment_coupon%` displays a ready-made block:

```text
🎫 Купон на следующую покупку

Скидка: 15%
GIFT15
```

This means Coupon for your next purchase; discount: 15%; code: GIFT15.

The automatic message for users who did not renew uses different values: `Code: %promo_code%` → `Code: RETURN15`, `Discount: %promo_discount%%` → `Discount: 15%`, and `Until: %promo_expires_at%` → `Until: 10.01.2030`.

**Compatibility and missing data.** Older supported Russian names still work in saved texts. In notifications, `%ключ_имя%` and `%key_name%` mean the key name; `%ключ_дней_до_окончания%` and `%key_days_left%` mean the number of days, not detailed remaining time. `%key(field=time_left)%` has no Russian alias. `%no_tariffs%` and `%без_тарифов%` become empty strings and do not hide plans or buttons.

The compatible ready-made blocks `%profile%`, `%keys_summary%`, `%key_info%`, `%key_history%`, `%screen_data%`, `%key_replace_data%` and `%key_rename_data%` depend on the screen and saved formatting. For example, on a compatible profile page, `%profile%` can show name, Telegram ID and balance, but stays empty on an arbitrary new page. Use individual values from the tables for new text. Compatible `%support_title%`, `%support_instruction%`, `%support_status_title%`, `%support_status_text%`, `%promo_status_title%`, `%promo_status_text%`, `%key_status_title%` and `%key_status_text%` are also not universal ready-made texts: without the matching workflow data, they are empty. Edit the ordinary texts for those screens directly on the page through the Customizer.

When required data is absent, a known placeholder becomes empty but surrounding words remain. For example, `Key: %key(field=name)%` becomes `Key: `. Unknown names remain visible on rendered pages; the broadcast editor rejects unknown names and invalid parameters when saving.

The **Мои ключи (My keys)** page, key cards, key-operation statuses, payment-method plan selection, payment QR screen, payment-check statuses, crypto-payment handoff, balance-payment screen, demo-payment screen, user-facing payment-method buttons and additional informational pages are customized through YaAdmin / Yadreno Admin. See [YAADMIN_GUIDE.en.md](YAADMIN_GUIDE.en.md).

## Referral program

Under **Маркетинг (Marketing)** → **Реферальная система (Referral program)**, administrators can:

- enable or disable the program;
- choose a reward type;
- configure levels;
- change the percentage or bonus-day count;
- manage balances and individual multipliers on user cards.

An individual multiplier scales the base percentage of every enabled level. With a base of `30%`, `5.0x` gives an effective `150%`, while `1.25x` gives `37.5%`. The value is not capped at `100%`: to obtain `50%` from a base of `10%`, set `5.0x`.

Users see effective percentages including their personal multiplier on their referral page. Admin level settings still show base percentages, while user cards show the multiplier `x`. Balance rewards are rounded to the smallest monetary unit; day rewards are rounded upward to a whole day.

The entire program and each level use the standard **Включено | Выключено (Enabled | Disabled)** pair. The colored indicator shows the selected state.

A disabled level is hidden from users and excluded from calculations.

## Traffic accounting

The bot tracks key traffic and shows it to users and administrators.

Administrators can:

- inspect a key's traffic usage;
- set a plan allowance;
- fully reissue a key under an ordinary or custom plan;
- reset used traffic;
- start panel synchronization.

If a key exhausts its allowance, the bot can restrict access and show a clear message to the user.

Paid renewals remain cumulative: finite validity is extended and a limited plan's traffic allowance is added to the current upper limit. Buying an unlimited plan makes the corresponding validity or traffic unlimited. Renewing a perpetual key with a finite plan starts its new period at payment time; a perpetual plan keeps it without expiry.

## Panel synchronization

The admin panel helps reconcile bot and VPN-panel data.

Available actions:

- export bot data to the panel;
- import current panel state into the bot;
- clean up extra panel entries;
- remove bot keys that are already absent from the panel.

Both synchronization directions first perform a check only. The preview lists, per server, how many keys will be created or updated, validity/traffic changes, skipped entries and errors. Nothing changes until **✅ Применить изменения (Apply changes)** is pressed. If no changes are needed, no apply button appears.

After confirmation, the bot fetches fresh panel data and applies only the key set shown in the preview. While applying, new key creation, renewal, movement, replacement and deletion wait for completion. Page viewing, payments and other bot sections continue working. If another manual synchronization is already running, a second is not queued; the bot asks you to retry later.

Importing **from the panel into the bot** transfers only validity and traffic state. The plan, blocking, device count, access state and key placement retain their bot settings. Historical usage never decreases; unlimited traffic and perpetual validity are recognized correctly. An expired key changes only if the panel actually renewed it or made it perpetual.

Ordinary background reconciliation reads both limits from the client list it already fetches; it does not request each user's devices. If an administrator adds an old panel after enabling device mode, the bot preserves that panel's current limits, continues supported synchronization and logs a warning. New keys cannot be created there until the panel is upgraded.

Ordinary synchronization does not delete unrelated or orphaned panel clients. Use the separate **Очистить панель (Clean panel)** action with its own confirmation.

## Deleting keys

Delete a key from the user card or key card.

If the server is reachable, the bot deletes the key both locally and on the panel. If it is unreachable, the admin panel warns and offers forced deletion only for cases where the server is permanently lost.

## Telegram bottom buttons

Through **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)**, request buttons below the input field for **«Старт» (Start)**, **«Мои ключи» (My keys)**, **«Поддержка» (Support)** or other available user commands. Ordinary page buttons remain. Every `/start` sends a separate message with the keyboard and retains it in the chat; its text is also customizable. Navigating home by button does not repeat this message; without a configured keyboard, there is no extra message. No restart is needed. Buttons act as their corresponding commands even during data entry. See [YAADMIN_GUIDE.en.md](YAADMIN_GUIDE.en.md#telegram-bottom-buttons).

Clearing the setting, including during a full reset, stops keyboard installation and interpretation of its labels as commands. An already displayed keyboard is not explicitly removed.

## Resetting customization

The hidden `/custom_reset` command in the bot chat restores Telegram and Web/Mini App customization to the installed version's defaults. Send it as an administrator, review the actions, press confirmation, then send the exact phrase displayed in your interface (`СБРОСИТЬ КАСТОМЫ` in Russian).

It resets custom texts, pages, routes and extensions, plus the published Web design, unpublished edits, preset and initial theme. The editor bar updates with the bot independently of the design. Before resetting, the bot creates backups and reports their locations.

Users, subscriptions, payments, history, website connection, domain and HTTPS are preserved. After success, reopen the Mini App or reload the website. If an error stops the operation, use the completed stages in the report: some changes may already have been applied. This command resets all customization together; ordinary bot updates continue preserving it.

## Backups

Open **Настройки бота (Bot settings) → 📦 Создать бэкап (Create backup)**. The bot builds an archive and sends it privately to you. Its name includes date and time, and its caption identifies manual creation. Daily archives still go to all administrators. Local copies are kept for seven days.

Both archives contain the bot database, saved settings and customizations, extension files, website and Mini App design with its signing key, and available VPN-panel backups. If a panel does not provide its backup, the archive caption includes a warning.

New backups exclude server configuration and HTTPS certificates. Transfer the server environment separately or configure it on the destination server. This also applies to local copies and backups before updates or customization resets. Restoring the application does not restore server settings from an older backup; the design signing key is retained.

Files users upload through website or Mini App forms are excluded, even if an extension saved them long term. Attachments already sent to support remain accessible through Telegram correspondence.

New archive messages include **📥 Загрузить или восстановить этот бэкап (Download or restore this backup)**. It also works after moving the same Telegram bot to another server:

1. Install and start a compatible version there with the same bot token and administrator access. Stop the previous bot instance.
2. In the previous chat, press the button below the desired archive. The bot downloads it from Telegram and validates it, even if the destination has no local copies.
3. Review what will be restored. **✅ Восстановить (Restore)** replaces current data with the selected copy and restarts the bot. All later changes to that data will be lost. **❌ Отмена (Cancel)** leaves working data unchanged.
4. Wait for the result message. After a move, run **«Подключить сайт и Mini App — домен и HTTPS» (Connect website and Mini App — domain and HTTPS)** in the installer, then check the website and account area.

Installed code and bot connection configuration are preserved. VPN-panel copies stay inside the archive and do not replace remote panel data. The signing key transfers, allowing continued design editing after restoration.

The standard Telegram Bot API lets bots download files up to **20 MB**. If Telegram does not deliver the archive or validation fails, working data is not replaced. If applying fails, the archive and diagnostics remain and the message reports the actual result. Restoration has no automatic rollback. Older messages without a button are not updated automatically.

## Updates

Methods are listed from the primary method to emergency recovery. Start with item 1 and move on only if the previous method is unavailable or unsuitable.

Status symbols:

- ✅ — protection exists and runs automatically;
- ⚠️ — a limitation or risk applies;
- ❌ — no protection; a failure requires manual action.

A database snapshot and automatic rollback are different. A verified snapshot is a recovery point. Automatic rollback means the bot restores the previous Git version and database itself after a failed update.

The update-check screen shows **Текущий релиз (Current release)** and **Текущий коммит (Current commit)** separately. It distinguishes ordinary updates, mandatory intermediate releases and test releases.

### 1. Ordinary admin-panel update — the primary method

▶️ Start: **Настройки бота (Bot settings)** → **Обновления (Updates)** → check for updates and confirm installation.

🎯 Use for planned updates. Install a test release only if you knowingly accept its risk.

- ✅ Blocking releases — respected; the first required intermediate release is installed first.
- ✅ Database snapshot — created and verified before changing the Git version.
- ✅ New-version validation — dependencies, migrations on a database copy and full startup must succeed.
- ✅ Automatic rollback — a failure after the Git change restores the previous Git version and database.
- ⚠️ Local code — an ordinary release will not install over modified Git-tracked files; a mandatory intermediate release uses an exact reset and may overwrite them.

**Обновление запущено (Update started)** confirms only that work has begun. A separate result arrives after the new version's startup is verified.

If **Обновление не началось (Update did not start)** cites file ownership or permissions, the current version keeps running. The message lists problematic paths, current and expected owners, permissions and the number of violations; the log has the complete list. Ask the server administrator to correct those settings before retrying. The bot does not automatically change existing owners or permissions.

### 2. Console soft update — the standard fallback

▶️ Start: run the script and select **2) 🔄 Мягкое обновление (Soft update)**:

```bash
bash <(curl -sL https://raw.githubusercontent.com/plushkinv/YadrenoVPN/main/install.sh)
```

🎯 Use when Telegram is unavailable or an admin-panel update could not start specifically because the maintenance process failed to launch.

The current installer selects the latest stable release by default, skipping commit subjects beginning with `?`. For unattended operation, append `update` to the script invocation; the optional next argument specifies a commit or branch, including a beta. If a newer beta is already installed, a soft update does not downgrade it to an earlier stable release.

- ✅ Blocking releases — respected; only the first required intermediate release is installed.
- ✅ Database snapshot — created and verified before changing the Git version.
- ✅ New-version validation — the same full verification as item 1.
- ✅ Automatic rollback — a failure after the Git change restores the previous Git version and database.
- ⚠️ Local code — tracked files must be clean and the transition must be a safe forward update; unrelated local files are not deleted.

### 3. Forced overwrite through the admin panel

▶️ Start: press **⚠️ Принудительно перезаписать (Force overwrite)** in Updates and confirm the warning.

🎯 Use when local changes or diverged Git history prevent an ordinary update and you intentionally want to restore official code.

- ✅ Blocking releases — respected; an active block stops the operation, and only the first pending mandatory release is installed.
- ✅ Database snapshot — created and verified before overwriting tracked files.
- ⚠️ Local code — tracked files are overwritten; unrelated local files are not deleted. `config.py`, the database, `backup/` and local extensions are preserved.
- ❌ Full new-version validation — absent; migrations run on the live database during ordinary startup of the new version.
- ❌ Automatic rollback — absent; select the created snapshot manually if something fails.

### 4. Hidden `/update` command — emergency soft update

▶️ Start: send `/update` to the bot as an administrator.

🎯 Use only when standard methods are unavailable and compatibility between the current and target versions has already been checked.

- ❌ Blocking releases — not respected; the command may skip a required intermediate release.
- ✅ Database snapshot — created and verified before changing the Git version.
- ⚠️ Local code — uses ordinary `git pull`, not a hard reset; modified tracked files stop the update and unrelated files are not deleted.
- ❌ Full new-version validation — absent; migrations run on the live database during ordinary startup of the new version.
- ❌ Automatic rollback — absent; failure requires manual rollback.

Prefer item 2 over `/update` where possible: the console method respects release ordering and supports automatic rollback.

### 5. Console hard overwrite — the last resort

▶️ Start: run the script and select **3) ⚠️ Жёсткая перезапись (Hard overwrite)**. From the installed bot directory, the equivalent command is `bash install.sh reset`.

The current script selects the latest stable release. `reset [COMMIT_OR_BRANCH]` runs without prompts; an explicit target, including a beta, takes priority. For older installations, use the current GitHub script. Installation and reinstallation also support unattended invocation: append `install 'BOT_TOKEN' 'ADMIN_ID'` to the script command.

🎯 Use if the repository is damaged, a soft update is impossible, or you need to restore all official code from GitHub.

- ❌ Blocking releases — not respected; required intermediate releases can be skipped.
- ✅ Database snapshot — the bot stops, then creates and verifies a snapshot before overwriting code.
- ⚠️ Local code — tracked files are overwritten and unrelated project files may be deleted. `config.py`, the database, `backup/`, local extensions, logs and the virtual environment are preserved.
- ⚠️ New-version validation — only a brief service-status check; migrations run on the live database at startup.
- ❌ Automatic rollback — absent; dependency or startup failures require manual rollback.

### Manual rollback

▶️ In Telegram: **Настройки бота (Bot settings)** → **Обновления (Updates)** → **↩️ Откатить обновление (Roll back update)**.

▶️ In the console: choose **4) ↩️ Откат обновления (Roll back update)** in `bash install.sh` or use `bash install.sh rollback SNAPSHOT_ID < /dev/null` for an already selected snapshot; this unattended command applies the rollback without further questions.

- ✅ Available points — up to three recent verified snapshots no older than seven days; the list shows version, short commit and creation time.
- ✅ Restoration — the chosen Git version and database return to their snapshot state.
- ⚠️ Data loss — all database changes since the snapshot are lost, including users, payments, keys and settings. Tracked files are also overwritten.
- ✅ Preserved — `config.py`, local extensions and VPN-panel databases.
- ✅ Rollback protection — current state is saved first; if rollback fails internally, the bot attempts to restore it.

The bot shows current and target versions and asks for confirmation. Console rollback uses the runner from the snapshot itself, so it remains available even when current-version files are damaged.

Use YaAdmin / Yadreno Admin for update-notification configuration and diagnostic help.

## Logs

Under **Настройки бота (Bot settings)** → **Скачать логи (Download logs)**, you can:

- download the full log;
- download the error list;
- clear current logs;
- send a log to YaAdmin / Yadreno Admin for analysis.

Agent-assisted log analysis is covered in [YAADMIN_GUIDE.en.md](YAADMIN_GUIDE.en.md).

## Extensions

In the main admin menu, **🧩 Расширения (Extensions)** opens a short summary: loading state, directory presence, last load, file count and result. A separate line shows **Редактирование ядра: запрещено/разрешено (Core editing: forbidden/allowed)**.

The first row, **Загрузка включена / Загрузка выключена (Loading enabled / Loading disabled)**, explicitly selects the loader mode. It is saved immediately but takes effect after a bot restart; the current process does not load or unload extension code dynamically.

The second row, **Запрещено / Разрешено (Forbidden / Allowed)**, controls core editing by the Customizer, including `/yaa` and Mini App editing. Core editing is forbidden by default: interface, settings and user extensions remain editable, while core-file changes are automatically reverted. **Разрешено (Allowed)** permits core edits, which may complicate updates. This setting applies without restarting. **🤖 Yadreno Admin** ignores it. Pressing the selected button again retains that state.

**📋 Подробности (Details)** opens diagnostic lists:

- files loaded successfully;
- files that failed;
- capabilities registered by extensions;
- event handlers and deferred tasks that completed, await retry or stopped, with attempt counts and errors;
- page-check and hook call/failure counts;
- counts of stock, new custom and compatible legacy pages, plus a bounded list requiring inspection.

If a loaded extension has settings, its name appears as a button on the main screen. Inside, toggle switches with buttons and enter ordinary values as messages. Secret values are masked and can be changed or cleared.

Extensions can add Telegram commands. After successful loading and a bot restart, a command works when typed manually, but restarting does not add it to Telegram's menu. The bot owner adds menu commands through BotFather. If a command does not open the expected screen, inspect extension diagnostics and loading errors.

Details reflect the current bot run. Page checks and hooks show aggregate counters; a separate list contains only items with accumulated errors or temporarily open protection. Temporarily disabled items show remaining wait time. There is no separate protection-reset button.

**🔄 Обновить (Refresh)** appears only in Details. It refreshes displayed diagnostics without restarting the bot, resetting protection or reloading extension files. **⬅️ Назад (Back)** returns to the summary and switches.

Compatible legacy pages continue working; their list supports planned review during future customization. A page with unknown classification does not open. This indicates incomplete or incorrect creation; give its name to the Customizer.

## Supporting the author

**💚 Поддержка автора (Support the author)** contains payment details and links for supporting project development.

It has no effect on bot operation and exists only for voluntary support.

## Web and Mini App: everyday settings

An account registered by phone without Telegram receives a persistent name, such as «Ядрён батон 001». Its three digits come from the phone number's suffix at registration; if all names with that suffix are taken, a longer random suffix is selected. Changing the phone, password or name list does not rename an existing account. After linking Telegram, its Telegram name is displayed and the assigned name is retained.

The name list is a hidden setting. Ask the Customizer: “Replace the names for new accounts without Telegram with: Northern Wind, Quiet Harbor, Polar Star.” Supply at least three distinct nonempty names; language and word count are unrestricted. The new list applies immediately to future assignments and does not rename existing accounts. A full customization reset does not reset this list. Saved custom designs survive updates; if needed, ask the Customizer to display the assigned name on their account card.

To add Telegram sign-in alongside phone/password, open the @BotFather Mini App and select your bot → **Login Widget**:

1. If **Switch to OpenID Connect Login** appears, press it.
2. Add the website's HTTPS address with a trailing `/` to **Redirect URIs**.
3. Add the same address without a path or trailing `/` to **Trusted Origins**.
4. Keep **Signing Algorithm → RS256 (Default)**.

If BotFather instead shows a common **Allowed URLs** list, add the permitted addresses there. **Client ID** is determined automatically from the configured bot; this login method does not use **Client Secret**. Neither needs entering in our bot. Then enable **Вход через Telegram (Telegram sign-in)** under **«Настройки → Веб и Mini App» (Settings → Web and Mini App)**. It is initially disabled after updating. Canceling Telegram sign-in leaves the ordinary form available.

Ordinary Telegram sign-in opens the service account linked to Telegram. To merge accounts, the user signs in by phone, selects **Привязать Telegram (Link Telegram)**, confirms in the bot and completes the action on the website. The screen shows the resulting account and transferred number. The transferred phone number and password replace the previous website login credentials of the account linked to Telegram; balances are combined, subscriptions remain separate, and payments and support requests are retained. Replace the old referral link with the main account's link. If the response is lost, sign in again with the transferred phone/password or through Telegram.

**«Настройки → Веб и Mini App» (Settings → Web and Mini App)** shows the active URL. Web can be enabled after its connection is prepared; this card cannot edit the domain or HTTPS. Domain and HTTPS setup belongs to the installer, not the everyday settings switch.

In the installer, select **«Подключить сайт и Mini App — домен и HTTPS» (Connect website and Mini App — domain and HTTPS)**. Enter a dedicated domain or subdomain; no connection-mode selection is required. The wizard installs Nginx, adds the website to a compatible existing Nginx, or recognizes a prepared local connection and enables Web. Before completion, it verifies that HTTPS serves your bot and admits the allowed attachment size. This check can transfer about 50 MiB of test data; it creates no Customizer task. On failure, the wizard gives the reason and instructions. The domain must point to this server, with ports 80 and 443 free or served by that Nginx. Panel and subscription certificate renewal must coexist with Nginx. The website uses ordinary Certbot and its standard automatic renewal. The wizard does not rewrite renewal for other certificates; conflicts produce manual preparation instructions. The certificate contact defaults to `admin@<specified-domain>` and issuance terms are accepted without a separate question.

For a remote proxy or an unrecognized arrangement, the administrator prepares the connection first, then finishes with the maintenance command supplied by the installer.

An incompatible configuration prominently shows **«Автоматическая установка невозможна. Требуется дополнительная ручная настройка сервера» (Automatic installation is unavailable. Additional manual server configuration is required)**. Between **«НАЧАЛО ИНСТРУКЦИИ ДЛЯ КОПИРОВАНИЯ» (START OF COPYABLE INSTRUCTIONS)** and **«КОНЕЦ ИНСТРУКЦИИ» (END OF INSTRUCTIONS)** are reasons, recommendations, and commands to recheck and install. Follow them yourself or copy the entire block to [Yadreno Admin](https://t.me/YadrenoAdmin_Bot), supplying server access separately. The agent first repeats the check and resolves only the remaining causes. The instructions include a separate agent-launch method that survives bot restarts. A launch message is not proof of readiness; wait for the wizard's final result. Moving inbounds, sharing one port between VPN and website, and unknown renewal arrangements remain separate tasks. If an unused inbound occupies 443, disable it in the panel and recheck. For a working inbound, especially Reality, prepare the complete cutover and recovery procedure first; moving it as an isolated preliminary step can make the VPN unavailable. The agent checks the website and available server evidence for affected services, including changed certificate renewal. You may separately check Telegram Mini App login and ordinary VPN, panel and subscription use; the agent need not wait for your confirmation of those user checks.

The standard account-area button appears after successful verification. Ordinary bot installation remains possible without enabling the website.

Unmodified design updates with the bot. After any edits, including unapplied ones, both your design and working account area are preserved. Updates do not mix them with the stock version. Unapplied edits do not activate themselves. Review the preview before applying.

The first row contains **«Подтверждение обязательно» (Verification required)**. Enabling it reveals three methods: **⭐ Звонки (Calls)**, **Моб.Авторизация (Mobile authorization)** and **СМС (SMS)**. Select only one; its settings button appears below. Verification is disabled by default, with calls selected. Disabling verification or switching methods preserves credentials.

Until the selected provider's credentials are complete, registration proceeds without phone verification and password recovery is unavailable. The card clearly shows this state. Once configured, verification applies to registration, adding or changing a number, and password recovery. Ordinary sign-in remains phone/password. If a configured provider is temporarily unavailable, verification stays required; another method is not enabled automatically.

- **⭐ Primary option — Ucaller calls.** Register through the [author's referral link](https://ucaller.ru/?invite=26951). Under **«Мои сервисы» (My services)**, create a service and copy its identifier and secret key to the settings card. The user enters the caller number's last four digits without answering. This is a budget verification option; check current pricing in Ucaller.
- **SMS Aero mobile authorization.** Register at [smsaero.ru](https://smsaero.ru/), obtain an API key under **«Настройки → API и SMPP» (Settings → API and SMPP)** and connect a sender name for mobile authorization. Enter email, API key and that name. The user confirms on their SIM card; if the provider switches to SMS, the form displays a code field. The callback address is generated automatically from the connected website URL.
- **Ordinary SMS through SMS Aero.** Enter email, API key and an approved SMS sender name. For timely code delivery, disable manual moderation by signing the provider's agreement in your account. Messages include the bot's display name and a six-digit code.

SMS Aero email and API key are shared by both methods; sender names are separate. Select a field in the card: the input screen provides the provider link and explains where to find the value. Ucaller also shows the author's referral link. Send the value as a message, or `-` to clear it. The input message is deleted and a saved secret is not shown again. Do not send provider passwords or keys in support conversations.

Verification lasts five minutes. If sending is unconfirmed, wait for the call or code; another request requires pressing the button after a pause. For SIM-PUSH, the form polls status itself; do not resend the request.

The standard account area automatically shows your bot's name and avatar in the mobile header and desktop sidebar. Change them through @BotFather. With Web enabled, changes are checked every five minutes and appear on the next account-area refresh. Temporary Telegram failure retains the last fetched profile. Without a photo, only the name appears; before the first successful fetch, the title is **«Личный кабинет» (Account area)**.

This section has no **«Модули» (Modules)** or **«Оформление» (Design)** entries. Select a ready-made design in the Mini App editor bar described below. Ask the agent for a custom header, colors or other design changes. The user's light/dark theme switch remains at the top of the account area.

The Web card shows the current interface status. When a new version is ready, the open account area offers to refresh its interface.

For a preview, open the Mini App using this section's button in a private bot chat. After administrator authorization, a compact **«Редактор · Главная» (Editor · Home)** bar appears above bottom navigation, showing the page name. Its second row contains **«Предпросмотр» (Preview)**, a switch and **«Черновик»/«Применено» (Draft/Applied)** status. Successful actions do not add separate bar notifications. Enable the switch to preview; disable it to return to the real account area. The page name or up arrow opens the editor. The input starts at two lines and can be resized or expanded through **«…»**. A response appears after the agent replies. The down arrow or tapping the preview page collapses the editor. **«Выйти из предпросмотра» (Exit preview)** in the menu returns to the real account area. Ordinary website visitors and users have no editor bar.

The bar, voice input and editor settings update with the bot even if you have a saved design or unpublished edits. Building, applying, restoring and resetting design affect only the application. A broken page or failed resource load leaves the editor available for repair. Reopening the administrator account area offline may leave the application unloaded until connectivity returns; the bar and reload remain available.

The gear opens settings; the down arrow collapses them while retaining the preview. Page, scenario and theme choices affect only preview: subscriptions and orders are synthetic, while plans and trial offers are your own. While the interface is stock, you can try three ready-made designs. **«Сохранить оформление» (Save design)** saves the choice for everyone; **«Отменить и выйти» (Cancel and exit)** cancels it. These buttons sit together, with an outline around Cancel. An unchanged selection shows **«Готово» (Done)**. Errors preserve the selection for another save. After applying a custom design, the label is **«Свой дизайн» (Custom design)**; an unapplied draft does not affect that label. These settings do not apply the agent's draft. Users choose light/dark mode with the top switch. Subscription import is available in Profile and **«Ещё» (More)**.

The standard account area suggests Happ for connecting. **«Добавить в Happ» (Add to Happ)** hands the subscription directly to the selected app; **«Как подключиться» (How to connect)** opens instructions. **«Назад» (Back)** returns to the originating screen, including import opened from Profile or More. App selection lets users choose a device and another client, including INCY; an explicit choice is remembered. Selection opens the app description with download links and connection steps. V2Box offers Google Play and App Store. Where automatic import is unavailable, a link and QR code are provided. Ask the Customizer to change the app list, order or presentation.

Home shows one selected subscription. The arrow next to its name switches it in place; viewing another card under **«Подписках» (Subscriptions)** does not change the Home selection. The selected subscription is marked **«На главной» (On Home)** in the list. Purchase and renewal offer ready-made plans, each including duration, limits and price. All three designs keep the same action layout. Profile, payments, available bonuses, import and help are grouped under **«Ещё» (More)**; the editor opens from the bottom bar.

Enter a request, attach files with the paperclip, or press the microphone. After **«Отправить» (Send)**, animated dots appear beside the button and before the status: first **«Отправляем запрос…» (Sending request…)**, then the agent's current status. They disappear when the task completes or sending fails. With reduced motion enabled on the device, the dots remain static and status text stays visible. Recording displays elapsed time and a sound meter. **«Стоп» (Stop)** ends recording; you can play, pause, delete or send it with a message. Collapsing the editor or opening settings retains text and attachments, ends recording and stops playback. Reaching the recording limit stops recording without sending it. After a microphone error, **«Проверить снова» (Try again)** retries; **«Написать текстом» (Write text)** remains available. A player error does not delete the recording.

The paperclip accepts images, text and Markdown, configurations, source files and OGG/Opus/WAV audio. Combine text, files and one microphone recording, or send files alone. The total limit is five files, including voice, each up to 10 MiB. Remove attachments individually; they clear after the message is accepted. You can also paste an image, screenshot or file into the message field with Ctrl+V/Cmd+V on desktop or **«Вставить» (Paste)** on mobile. It appears in the same attachment list and is sent only when you press Send. If Telegram or the browser does not provide clipboard files, use the paperclip. Ordinary text pasting is unchanged; file limits are identical for both methods. To place your own image, attach it and describe the destination. The temporary original is removed after seven days; the copy saved in the design remains.

Editing requires a connected Yadreno Admin Customizer; preview works without one. “Show it without applying” leaves a validated draft. **«Применить» (Apply)** applies the displayed validated version immediately, without another agent request or rebuild. Typed text, files and voice remain. **«Применено» (Applied)** appears after publication is confirmed. If the response is lost, refresh status; reapplying the same version does not duplicate anything. You can also ask the agent to change and apply the design directly. Working edits save automatically; there is no separate named-draft save button.

**«Обновить статус» (Refresh status)** and **«Новый чат» (New chat)** are in **«…»**. Stop appears for an active task. New chat returns preview to the published version while preserving the typed message and working edits. A stop acknowledgement does not mean changes were rolled back. Closing the bar does not cancel an accepted task. If preview is unavailable, retry loading it or open the currently published version without losing input.

Preview also works without a new draft: before the first task, after an ordinary answer or after cancellation, the opened version remains displayed. Absence of a draft is not an error. **«Черновик»/«Применено» (Draft/Applied)** refers to the displayed version. If a draft is outdated or its files unavailable, the editor explains why; it cannot be applied.

A temporary connection loss shows a small icon beside settings. Click or hover to read the explanation. The page, typed text, files and recording remain. Connectivity is checked automatically; after a successful refresh, the icon disappears and the task need not be repeated.

A sending error preserves text, voice and files and is not cleared by background polling. Its code helps the developer diagnose the issue. **«Не удалось подтвердить отправку. Запрос мог быть принят» (Could not confirm sending. The request may have been accepted)** means the outcome is unknown: press **«Проверить статус» (Check status)**. If it remains unconfirmed and no task is active, **«Отправить повторно» (Send again)** becomes available; retrying may create another task. There is no automatic resend.

Installation and updates prepare design-build tools in advance. If preparation temporarily fails, the bot and published website keep working. Preparation retries automatically on the next startup or update. The editor supports Ubuntu 22.04 and 24.04. If it reports an incompatible environment, retain the reason and diagnostic ID for the developer. Simply resending the task does not fix that cause.
