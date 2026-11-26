# Yadreno Admin: built-in agent guide

This guide covers only the Yadreno Admin agent built into YadrenoVPN. It does not describe ordinary admin-panel functions or the bot's internals. For administration through menu buttons, see [ADMIN_GUIDE.en.md](ADMIN_GUIDE.en.md).

## Language and text customization

To reveal a hidden payment card, ask: “Show Stripe in administrator payment
settings” or “Show Ya.Seller settings.” The agent changes only card visibility.
Then enter credentials and enable the method through the ordinary admin panel.
“Hide Stripe in administration” does not disable customer payments or checks of
existing invoices. Initial visibility follows the installation language; an
explicit agent setting overrides it. A full customization reset restores initial
visibility while keeping credentials and method enablement.

The agent receives the installation language in every conversation, including page, broadcast and website editing. Responses from the external agent are not translated automatically.

To change bot text, open the page and use `/yaa`, or ask the Customizer. For the website, you can request individual text changes or a language switch for your own website. All 13 dictionaries are included in the editable application; check the result in preview and publish normally. This does not change the Telegram bot, admin panel or system editor language. Updates preserve the modified website, its dictionaries and unpublished changes; older custom websites are not rewritten automatically.

## When the required knowledge base is not ready

After a bot update, the agent may report that the required knowledge-base version has not been uploaded or is not ready. The request does not start: forward the message to the Hub administrator so they can upload the specified bundle and wait for indexing. If the message requires a Hub update, update Hub first. Replacing your API key or repeating the task description does not resolve this issue.

## Choosing the right tool

The built-in agent has four separate tools:

| Tool | Purpose | When to use it |
| --- | --- | --- |
| **`/yaa` on a user page** | Edit the open page | When the screen to change is already in front of you |
| **`/yaa` in Broadcast** | Prepare a broadcast and its audience | When drafting or revising a broadcast |
| **🤖 Yadreno Admin — «Админ» (Admin)** | Administer the server hosting this bot | When checking, configuring or repairing a running service |
| **🛠 Кастомизация YadrenoVPN — «Кастомизатор» (Customizer)** | Customize this bot, website and Mini App | When making lasting interface or user-workflow changes |

The distinction is straightforward:

- **Админ (Admin)** maintains the server and running VPN service;
- **Кастомизатор (Customizer)** changes the bot, website and Mini App for users;
- **`/yaa`** quickly edits a particular screen or broadcast in its current context.

The Admin and Customizer start screens highlight tool-selection guidance in bold with 🧭. To stop the current request in either chat, send `/cancel`.

### Core editing

The setting is under **🧩 Расширения (Extensions)** in the main admin menu. **Запрещено / Разрешено (Forbidden / Allowed)** selects the state explicitly without restarting. The Customizer displays the current state on its start screen.

- **Запрещено (Forbidden)** — interface, settings and user extensions remain editable; core-file changes are automatically reverted. This is the default.
- **Разрешено (Allowed)** — the Customizer may edit core files. This can complicate bot updates.

The setting also applies to page editing through `/yaa` and the Mini App. **🤖 Yadreno Admin ignores this setting** and may change system project files to carry out your tasks.

## First connection

**🤖 Yadreno Admin** and **🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)** are in the main admin menu. `/yaa` uses the same access.

If the agent is not connected:

1. Open [@YadrenoAdmin_Bot](https://t.me/YadrenoAdmin_Bot).
2. Under **Профиль (Profile)**, create an access key for YadrenoVPN.
3. Return to your bot, press **🔑 Указать api_key (Set api_key)** and send the key. Send only the key on one line, without quotes, a caption, explanations or embedded spaces.

If the bot moved to another server and the old key is no longer accepted, create a new key and press **🔑 Заменить api_key (Replace api_key)**.

**«Ключ Yadreno Admin повреждён» (Yadreno Admin key is damaged)** means the saved value cannot be used safely. Press **🔑 Заменить api_key (Replace api_key)** and send only the complete key on one line. The bot will not extract it from an explanation or automatically repair the saved value.

## Images from the agent

Ask, for example, “Draw a cover for the summer promotion.” Telegram delivers the original as a file alongside the answer. In the Web editor, use **Download** beside the filename.

You can specify the destination in the same request, for example “Create a cover and put it in my broadcast draft” or “Draw an illustration for the help page and add it to that page.” Preview the broadcast and launch it with the usual button. For a website, ask the agent to add the image to the design, check the preview and apply the changes when ready.

## Voice messages and audio recordings

All four tools accept voice messages and audio recordings. In **Админ (Admin)** and **Кастомизатор (Customizer)**, open the appropriate chat and send voice. For a page or broadcast, send `/yaa` without text; the bot asks what you want to do. Then send voice, a recording or text. The agent does not start before that message. **⬅️ Назад (Back)** closes input.

A voice message is treated as your request. An ordinary audio recording, including one sent as a file, is material for analysis: add the task in its caption or describe it in the conversation. The agent asks for clarification if the task is unclear.

OGG/Opus and WAV are supported up to **10 MB per file**. The bot shows transcription status, then the agent's answer. A full transcript is not posted automatically; request it separately if needed. Ordinary cancellation and status checking become available once processing starts. A voice request to the broadcast editor prepares a draft; sending still requires separate confirmation.

## 1. `/yaa` — current-page editor

### Purpose

This is an “edit what you see” mode. The agent receives the open user page as its initial context. You can explicitly ask it to change another page, the website or several pages together. The command works on all successfully opened saved user pages, including compatible pages created by older versions. There is no need to find the screen in a separate list.

### How to use it

1. Open the page to change.
2. Send `/yaa`, wait for the bot's invitation, and describe the result using text, voice or an audio recording. You can also send `/yaa` together with the task text immediately.
3. After the first answer, continue with ordinary messages; there is no need to repeat `/yaa`.

To replace media, attach a photo, video or GIF with the command and explain what to do in its caption.

### Starting a new conversation with `/yaan`

Send `/yaan` with a task to start a fresh conversation and submit the request immediately. Without text, it starts a new conversation and shows the usual input invitation. If the agent is busy, the bot shows the current task and available cancellation; after canceling manually, send the request again. The command itself cancels nothing. The broadcast editor likewise starts a new conversation only once.

### Sending website images from Telegram

Attach an image in the Customizer chat or with `/yaa`/`/yaan` and describe where it belongs. You do not need to open the target web page first. The web bar and Telegram offer the same Customizer capabilities, but their conversations stay separate. Uploaded files remain available for seven days; a copy placed on the website survives independently of the temporary original.

### What you can request

- rewrite or shorten the open page's text;
- replace or remove media;
- rename, show, hide or reorder available buttons;
- change a button's color, icon or link;
- assign different colors to individual plans or servers on the open page;
- give the page the desired style.

Examples:

- `/yaa shorten the heading and make the text friendlier`;
- `/yaa rename the «Купить ключ» (Buy key) button to «Connect VPN»`;
- `/yaa make the support button green and link it to @my_support`;
- `/yaa on this page, make the «Месяц» (Month) plan blue and «Год» (Year) green`;
- `/yaa put this photo on the current page`;
- `/yaa remove the media and keep only text`.

### Tool boundaries

The open page supplies initial context. At your request, the Customizer can change other pages or several bot/website screens together. Presentation does not replace the underlying payments, keys, subscriptions or other bot functions. Changing a button's label or appearance does not override its action rules.

Name another page in the task, or open it and run `/yaa` there. The **Кастомизатор (Customizer)** chat is also available for tasks not tied to the current page.

For a saved custom login page, ask: “Add Telegram sign-in alongside phone and password while preserving the current design.” First configure your bot in @BotFather → **Login Widget**: press **Switch to OpenID Connect Login** if available. Add the website URL with trailing `/` to **Redirect URIs**, and the same address without a path or trailing `/` to **Trusted Origins**; keep **RS256 (Default)**. If there is one **Allowed URLs** list instead, add the addresses there. Client ID is determined automatically; Client Secret is not required. Then enable Telegram sign-in under **«Настройки → Веб и Mini App» (Settings → Web and Mini App)**. The Customizer may change account-merge presentation and explanations; core handles identity confirmation and fund transfers.

The same Customizer can change the hidden phone-account name list: “Replace names for new accounts without Telegram with Northern Wind, Quiet Harbor, Polar Star.” At least three distinct nonempty names are required; language and word count are unrestricted. The list applies immediately to future assignments. An assigned name survives phone, password and list changes; after linking Telegram, the Telegram name is displayed. Customization reset does not change the list. For a saved custom design, ask separately to display the assigned name on the account card while preserving the design.

## 2. `/yaa` — broadcast editor

### Purpose

Under **Маркетинг → Рассылка (Marketing → Broadcast)**, `/yaa` opens a separate broadcast editor instead of the page editor. It works with the current draft and understands that section's audience settings.

### Editor capabilities

- write a broadcast from scratch;
- shorten, rewrite or change the text's style;
- prepare a photo caption;
- add or replace a photo;
- create an ordinary poll or quiz;
- select recipients from described conditions;
- prepare content for preview, saving or sending.

Recipient conditions can be combined and apply simultaneously. Asking for a broadcast “to everyone” clears earlier selected conditions.

Examples:

- `/yaa prepare a message for users who took a trial and never purchased`;
- `/yaa write a short, friendly announcement about new plans`;
- `/yaa include each recipient's personal referral link`;
- `/yaa shorten this text and add a few emoji`;
- `/yaa create a poll about support quality`.

After the first request, continue normally: `make it shorter`, `change the heading`, `add a photo`, `show it only to users who bought a subscription`. Voice and audio recordings also work. To start with voice, send `/yaa` without text in Broadcast and wait for the invitation.

Page placeholders work in text and photo captions: `%user_name%` is the recipient's name and `%referral_link%` their personal referral link. For example:

```text
Hello, %user_name%! Your link: %referral_link%
```

With fictional data:

```text
Hello, Anna! Your link: https://t.me/ExampleBot?start=ref_demo
```

The bot inserts each recipient's personal data at send time; previews and **🚀 К запуску (Prepare to send)** show your own data. Referral links require the referral program to be enabled. Specific key or payment values are empty without a related event. Polls remain shared by all participants.

For a key-specific notification, `Key: %key(field=name)%` can produce `Key: Home`; `Days left: %key(field=days_left)%` produces `Days left: 2`; `Time remaining: %key(field=time_left)%` produces `Time remaining: 2 дн., 13 ч., 29 мин.` (2 days, 13 hours, 29 minutes with default labels). `Until: %key(field=expires_at)%` shows a date such as `Until: 2030-01-03`. These are different values; `time_left` has no Russian alias. Supported older names remain valid in existing texts. More examples appear in the [administrator guide](ADMIN_GUIDE.en.md#placeholders-what-to-write-and-what-the-user-sees).

### Drafts and sending

The editor always works through a draft and never sends a broadcast itself. Even “send to everyone” only prepares content for launch.

Actions below the editor's response:

- **👁 Превью (Preview)** — view the broadcast as a Telegram message;
- **💾 Сохранить (Save)** — apply the draft and continue;
- **🚀 К запуску (Prepare to send)** — save the draft and open the final preview;
- **🚪 Выйти (Exit)** — close the editor.

Actual sending starts only after separately pressing **✅ Разослать N (Send to N)** on the final screen. Changes to content, conditions or recipient count require renewed confirmation.

### Tool boundaries

The broadcast editor handles only the current broadcast. It does not change bot pages, automatic messages or server operation. Use the **Кастомизатор (Customizer)** for lasting user-message changes.

## 3. «Админ» — administering the current server

### Purpose

**🤖 Yadreno Admin** is a general administrator for the server hosting the current bot. It diagnoses, configures and maintains the running VPN service.

The built-in Admin is already on that server; you do not need to provide SSH access to the same machine.

### Tasks you can delegate

- check the bot, server and VPN panel;
- find and explain log errors;
- investigate failed key issuance or payments;
- help with users, keys, subscriptions and payments;
- configure or restore connected services;
- perform maintenance and verify the result;
- analyze an attached log, photo or document.

Examples:

- `Check the bot's status and explain whether there are any errors now`;
- `Find why key issuance stopped and fix it`;
- `Review the logs after the update and explain the problem in plain language`;
- `Check the VPN-panel connection and restore it`;
- `Check why this user's payment did not complete`.

### The single-server boundary

The built-in Admin serves **only the server hosting this bot**.

To administer another server or several servers, **use the main agent** [@YadrenoAdmin_Bot](https://t.me/YadrenoAdmin_Bot). Do not use the built-in agent as a control center for other servers.

Admin is not intended for page design or user-interface development. Use the **Кастомизатор (Customizer)** or contextual `/yaa` instead.

## 4. «Кастомизатор» — customizing this bot

### Purpose

**🛠 Кастомизация YadrenoVPN (YadrenoVPN customization)** changes the appearance and user-facing capabilities of this particular bot. It is not server administration or incident recovery. Its result is a bot that looks or behaves as your project requires.

### Customizer capabilities

- change text, media, buttons and presentation throughout the user interface;
- create informational pages and connect them with buttons;
- change navigation and user-section placement;
- translate the user interface into another language;
- customize messages and automatic notifications, including remaining subscription time in days, hours and minutes;
- set how many hours after registration an unattributed user can follow a referral link;
- add supported user workflows and local capabilities;
- request text, IDs or numbers, including decimal amounts, through ordinary messages and ask several questions sequentially;
- configure automatic actions after payment or a trial, including conditions based on purchase history and the inviter;
- create and activate promo codes and coupons within user workflows;
- apply direct discounts with prices shown during plan selection;
- create workflows that safely debit a user's balance and open a built-in support request;
- start a new support conversation with a selected user through a user workflow, retaining ordinary replies and operator assignment;
- customize an administrator's incoming support card: text, media, buttons and conditions for request-specific actions;
- move ordinary key purchase or replacement to a chosen menu location while preserving core checks, payment and recovery;
- create workflows that link a newly purchased key to a selected subscription belonging to the same user, or manage existing links between that user's keys;
- change several related screens together so they remain consistent.

For **📱 Мои устройства (My devices)**, the Customizer can change page text, button labels, device-row formatting and empty-list/error messages. Only the administrator switches **IP-адреса / Устройства (IP addresses / Devices)** under **Настройки бота (Bot settings)**; the Customizer does not enable the mode or bypass the 3X-UI version check.

Individual plans and servers can have different colors. Colors belong to a specific screen, so purchase and renewal can differ. List all relevant screens when requesting matching colors. Renaming or reordering a plan or server preserves its color.

Keys, devices and eligible subscriptions differ per user. These lists support one shared button color per page, not individual item colors.

Examples:

- `Translate the bot's entire user interface into English`;
- `Create a Service rules page and add a button on Home`;
- `Shorten purchase messages and give all buttons a consistent style`;
- `Add a balance top-up button to the user's profile and design the complete workflow`;
- `In my workflow, first request the recipient ID as a normal message, then the amount, then show confirmation`;
- `Add an exchange of 500 rubles from balance for a certificate, and open a built-in support request after the debit`;
- `Change the buttons below the administrator's reply in built-in support`;
- `Add a status and a Close request button to the administrator's incoming request card`;
- `Create a separate page with additional trial offers`;
- `Allow referral attribution within 24 hours of registration`;
- `Add a purchase button for the «Пакет трафика» (Traffic package) plan to my page, then after issuance ask which of the user's subscriptions it should join`;
- `Create a linked-subscriptions management page: show the user's keys and let them attach or detach an additional key`.

You do not need to explain implementation. Describe the desired user outcome; the Customizer chooses a suitable safe approach.

Incoming support cards are customized in this chat. Specify administrator actions and when their buttons should appear. The user's original text or file arrives separately; saved changes apply to new cards.

A custom linking workflow does not require a predefined lookup group. The Customizer can connect one user's keys from any groups in a chosen direction and create several such links.

### Telegram bottom buttons

Ask the Customizer: “Add ‘🏠 Start’, ‘🔑 My keys’ and ‘💬 Support’ buttons below the input field, while keeping ordinary page buttons.” You can change labels, rows and mappings to existing user commands, including loaded extension commands. Administrator commands are unavailable for this keyboard.

The keyboard appears on the next `/start`. The bot sends a separate keyboard message and retains it in the chat. Its text is customizable, for example: “⌨️ Keyboard ready. Choose a command below.” Each new `/start` sends this message again; navigating Home by button does not repeat it. No restart is needed. A button acts like its command, including during data entry. Manually sending the identical label also invokes that command.

To stop installing the keyboard, ask to clear the bottom-button setting. An already displayed keyboard is not explicitly removed, and its previous labels stop being interpreted as commands. Full customization reset clears the same setting. By default no bottom keyboard is configured and there are no extra messages.

### Customizer boundaries

In input workflows, any ordinary bot button or command cancels the current question. The chosen action may immediately ask another question. External-link, copy and Mini App launch buttons do not cancel input. Unfinished conversations do not survive a restart; users start the workflow again.

For a strictly single-use discount, request automatic coupon issuance and application so the user need not type a code. A direct “discount until first payment” condition is checked when an invoice is created, so several invoices created beforehand may receive that discount.

The Customizer:

- works only with this bot;
- does not administer the server or VPN panel;
- does not perform one-off operations on a particular user, key or payment;
- does not bypass security, payment or data-preservation rules;
- does not link keys belonging to different users, a key to itself, or links forming a cycle;
- does not receive subscription URLs, VPN-panel data or other secrets for linking workflows; it uses safe core actions;
- does not switch IP/HWID limit mode or modify registered devices outside the ordinary key card;
- does not replace Admin for incident diagnosis or server-state analysis.

A purchase started by a custom workflow can retain bounded context until actual key issuance. That context survives plan and payment-method selection, free purchases, restarts and background payment checks. After issuance, the workflow resumes safely with repeatable handling; a temporary extension error does not cancel payment or revoke an issued key.

The Customizer can also add a separate video tutorial immediately after the QR code for a newly purchased or trial subscription. Provide the video and desired text. Simply reopening a key does not trigger it; specify whether delivery errors should be retried. A separate notification can target someone else, such as a referrer or administrator. Explicitly describe the recipient and content.

You can configure a message when a particular key becomes inactive because its time or traffic is exhausted. Describe the text, recipient and extra conditions, such as sending only when the user has no other active keys. The reaction runs once per continuous inactive period; after access is restored, the key can become inactive again. This is separate from ordinary reminders about upcoming expiry.

Through the Customizer, set how many inactive days precede deletion from the panel and how many precede deletion from the bot. Both delays treat time and traffic exhaustion equally. If the key regains valid time and available traffic before deletion, the previous countdown resets.

Ask for a delayed action after a duration or on a specific date. For example: “Three days after registration, remind the user about a subscription if they have not bought anything,” or “Three days after key expiry, check for active subscriptions and send an offer if appropriate.” Specify timing, conditions, recipient and text. Saved tasks survive bot restarts; overdue tasks run after startup. Check their state in extension diagnostics. Full customization reset stops unfinished tasks.

For one open screen, `/yaa` is convenient. For several parts of the bot or a new user workflow, use the Customizer.

## Returning to default customization

For a full reset, send `/custom_reset` as an administrator, review the preview and confirm with the exact phrase displayed by the bot (`СБРОСИТЬ КАСТОМЫ` in Russian). It resets Telegram and Web/Mini App customization together: published design, unpublished edits, preset and initial theme. The base application design comes from the installed bot version. The editor bar updates with the bot independently of customization.

The bot creates backups first and reports their locations. Users, subscriptions, payments, website connection, domain and HTTPS are preserved. Reload the website or reopen the Mini App after success. On error, the report shows completed stages; reset may have applied partially. **«Новый чат» (New chat)** clears only the conversation and does not replace this command.

Pre-reset backups preserve application data and the design signing key. They exclude server settings and HTTPS certificates; move the server environment separately.

## Managing conversations

Each of the four tools has its own conversation. Page-editor, broadcast-editor, Admin and Customizer contexts are not mixed.

Main buttons:

- **🆕 Новый чат (New chat)** — start without previous conversation context;
- **🔑 Заменить api_key (Replace api_key)** — connect a different access key;
- **❌ Отмена (Cancel)** — stop the running task;
- **🔄 Ну чё там? (Any update?)** — request current task status or the last answer;
- **📄 Открыть ответ (Open answer)** — open the full answer if supplied;
- **🚪 Выйти (Exit)** — close the current conversation screen and return to the menu.

## Writing requests

Use ordinary language and explain:

- what is happening or what should change;
- where it should work;
- the desired result;
- what must remain unchanged.

For example:

`On the purchase page, replace the long description with a short one, but keep the existing buttons.`

`Check why keys are not issued after the update. Explain the cause first, then fix it and verify the result.`

`Create a section with iPhone and Android instructions and link to it from Help.`

Technical implementation instructions are unnecessary; a clear goal and expected result are enough.

## When a request does not start

- For **«Ключ Yadreno Admin повреждён» (Yadreno Admin key is damaged)**, press **🔑 Заменить api_key (Replace api_key)** and send only the key on one line, without quotes or explanation. The task is not submitted and has no cancellation or status buttons.
- If the key is rejected or revoked, create another in [@YadrenoAdmin_Bot](https://t.me/YadrenoAdmin_Bot) and press **🔑 Заменить api_key (Replace api_key)**.
- If the agent reports a plan restriction or quota, check your profile in the main Yadreno Admin bot.
- For temporary connection errors, retry later.
- In Telegram, status and cancel buttons appear only for tasks that actually started.

### Shared rules for the website and bot

The Customizer can use shared business rules for accounts with or without Telegram. Existing bot-page settings are preserved; website presentation is configured separately. Website correspondence requires a support module. Its requests appear in shared support, and administrator replies are retained for the website user. Domain and HTTPS management belong to the installer, not the page editor.

The standard account area gets its name and avatar from the bot profile. Change them through @BotFather; while Web is enabled, changes are checked every five minutes and picked up on account-area refresh. Without a photo, only the name is shown; temporary Telegram errors retain the last fetched profile. Ask the agent for custom header and colors. Ready-made designs are selected in the Mini App bar's settings.

After administrator authorization, a compact **«Редактор · Главная» (Editor · Home)** bar appears above bottom navigation, showing the page name. Its second row contains **«Предпросмотр» (Preview)**, a switch and **«Черновик»/«Применено» (Draft/Applied)** status. Successful actions do not add separate bar notifications. Enable the switch to preview; disable it to return to the real account area. The page name or up arrow opens the editor. The input starts at two lines and can be resized or expanded through **«…»**. A response appears after the agent replies. The down arrow or tapping the preview page collapses the editor. **«Выйти из предпросмотра» (Exit preview)** returns to the real account area. Ordinary website visitors and users have no editor bar.

The Customizer changes application pages, styles and behavior. The bot maintains the bar, voice input and editor settings; they survive design builds, publication, restoration and reset. A page error does not close the editor.

The gear opens settings; the down arrow collapses them while retaining preview. Page, scenario and theme choices affect only preview: subscriptions and orders are synthetic, while plans and trial offers are your own. With a stock interface, you can try three ready-made designs. **«Сохранить оформление» (Save design)** saves the choice for everyone; **«Отменить и выйти» (Cancel and exit)** cancels it. The buttons sit together, with an outline around Cancel. An unchanged selection shows **«Готово» (Done)**. Errors preserve the selection for another save. After a custom design is applied, **«Свой дизайн» (Custom design)** appears; an unapplied draft does not affect this. These settings do not apply the agent's draft. Users choose light/dark mode with the top switch. Subscription import is in Profile and **«Ещё» (More)**.

The standard account area's default client is Happ. Users can select another for their device, including INCY. Selection opens the client description, official downloads and instructions. **«Добавить в …» (Add to …)** opens a separate HTTPS handoff page for the chosen app. The browser may require another **«Открыть» (Open)** click. With Web/Mini App enabled, request the same button on the bot's subscription-delivery page. The generated URL is private like the subscription; account-area styling does not remove the launch page. **«Как подключиться» (How to connect)** opens instructions; **«Назад» (Back)** returns to the originating screen. Describe the desired app list, order, installation links or presentation to the agent; these belong to account-area design.

On compact Home, select a subscription with the arrow beside its name. Opening a card from **«Подписок» (Subscriptions)** does not change the Home selection; it is marked **«На главной» (On Home)**. A plan already includes validity and limits, with no separate duration selection. The three ready-made designs share one structure and open the editor from the bottom bar. Ask the agent to change layout or labels while preserving familiar actions and return navigation. A bot update does not automatically replace saved custom design with a newer stock design.

Enter a request, attach files with the paperclip, or press the microphone. After **«Отправить» (Send)**, animated dots appear beside the button and before the status: first **«Отправляем запрос…» (Sending request…)**, then the agent's current status. They disappear when the task completes or sending fails. With reduced motion enabled, the dots remain static and status text stays visible. Recording displays elapsed time and a sound meter. **«Стоп» (Stop)** ends recording; play, pause, delete or send it with a message. Collapsing the editor or opening settings retains text and attachments, ends recording and stops playback. Reaching the recording limit stops it without sending. After a microphone error, **«Проверить снова» (Try again)** retries, or choose **«Написать текстом» (Write text)**. A player error does not delete the recording.

The paperclip accepts images, text and Markdown, configurations, source files and OGG/Opus/WAV audio. Combine text, files and one microphone recording, or send files alone. Up to five files total, including voice, are accepted at 10 MiB each. Remove attachments individually; they clear after message acceptance. Paste images, screenshots or files with Ctrl+V/Cmd+V on desktop or **«Вставить» (Paste)** on mobile. They appear in the same attachment list and are sent only by pressing Send. If Telegram or the browser does not provide the clipboard file, use the paperclip. Ordinary text pasting is unchanged; both methods share the same file limits. To place your image, attach it and describe the destination. The temporary original is deleted after seven days; the copy saved in the design remains.

Editing requires a connected Yadreno Admin Customizer; preview works without one. “Show it without applying” leaves a validated draft. **«Применить» (Apply)** immediately applies the displayed validated version without another agent request or rebuild. Typed text, files and voice remain. **«Применено» (Applied)** appears after publication is confirmed. If the response is lost, refresh status; reapplying the same version does not duplicate anything. You can ask the agent to change and apply the design directly. Working edits save automatically; there is no separate named-draft save button.

**«Обновить статус» (Refresh status)** and **«Новый чат» (New chat)** are in **«…»**. Stop appears for an active task. New chat returns preview to the published version while preserving your typed message and working changes. A stop acknowledgement does not mean changes were rolled back. Closing the bar does not cancel an accepted task. If preview is unavailable, retry loading or open the currently published version without losing input.

Preview works without a new draft: before the first task, after an ordinary answer or after cancellation, the opened version remains visible. Absence of a draft is not an error. **«Черновик»/«Применено» (Draft/Applied)** refers to the displayed version. If a draft is outdated or its files unavailable, the editor explains why; it cannot be applied.

Temporary connection loss shows a small icon beside settings. Click or hover to read the explanation. The page, typed text, files and recording remain. Connectivity is checked automatically; after successful refresh the icon disappears, without repeating the task.

A sending error preserves text, voice and files and is not cleared by background polling. Its code helps the developer diagnose it. **«Не удалось подтвердить отправку. Запрос мог быть принят» (Could not confirm sending. The request may have been accepted)** means the outcome is unknown: press **«Проверить статус» (Check status)**. If still unconfirmed and no task is active, **«Отправить повторно» (Send again)** is available; retrying may create another task. There is no automatic resend.

The web-editor conversation is shared by the installation's administrators but separate from the ordinary Customizer chat and bot-page editing.

All three Customizer entry points work with one design, although their conversations are separate. Source backups are created automatically before each request and kept for seven days; the agent need not create them manually. Ask to discard unapplied edits or restore a backup. Applying a restored version first validates and builds it; failure preserves the working account area and current files. This restoration does not affect payments, subscriptions or VPN settings.

The Customizer can add website and Mini App forms accepting files up to 20 MiB and sending attachments to existing support, including without text. For example: “Add a form to send a PDF receipt to support.” This alone does not confirm payment. Temporary copies last seven days; specify long-term storage separately. User files are excluded from bot backups. Synthetic preview does not upload real files; test sending in an authenticated preview. If a new upload form rejects a permitted file after updating, rerun **«Подключить сайт и Mini App — домен и HTTPS» (Connect website and Mini App — domain and HTTPS)** in the installer to apply the current upload configuration. For an external HTTPS proxy, give the setup report to its administrator.

If the platform itself or its build environment is faulty, the Customizer reports an error ID for the developer. Do not repair the platform through the customization conversation.
