"""Static release checks for all offline catalogs, placeholders and interface limits."""
from __future__ import annotations

import ast
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
TOKENS = re.compile(r'%\w+(?:\([^%()\s]*\))?%|\{(?:p[0-9]+|count|client|confirmation)\}')
TAGS = re.compile(r'</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|span|tg-spoiler|blockquote|tg-emoji)\b[^>]*>')
INSTALLER_LITERALS = (
    '--language', 'BOT_TOKEN', 'ADMIN_ID', 'ADMIN_IDS', 'SNAPSHOT_ID', '@BotFather',
    'config.py', 'install.sh', 'origin/main', 'backup/pre_update', 'backup/', '/backup',
    'python3-venv', 'python3-pip', 'yadreno-vpn.service', 'git pull', '(Y/n)', '(y/N)',
    'bash install.sh install BOT_TOKEN ADMIN_ID [--language ru]',
    'bash install.sh install <BOT_TOKEN> <ADMIN_ID>',
    'bash install.sh rollback SNAPSHOT_ID',
)
INSTALLER_ACTIONS = ('install', 'update', 'reset', 'rollback', 'web-setup')
CORE_LITERALS = ('pages.image_custom', 'migration.log', 'backup/pre_update',
                 'bot.utils.custom_extensions', 'None', '@Ya_SellerBot')
CORE_KEY_LITERALS = {
    'payments.show_crypto_management_menu.2': ('USDT',),
    'users_keys_deleted.on_sync_deleted_panel_ask.1': ('user_', 'site_'),
    'broadcast_editor.validate_style_profile.3': ('style.{p0}',),
    'broadcast_editor.validate_style_profile.5': ('style.use_lists',),
    'broadcast_editor.validate_style_profile.6': ('style.custom_instructions',),
    'broadcast_editor.validate_style_profile.7': ('style.custom_instructions',),
    'broadcast_editor.stage_message.1': ('media_action', 'keep', 'remove'),
    'broadcast_editor.stage_style.1': ('style_scope', 'default', 'one_off'),
    'broadcast_validation.validate_generated_poll.6': ('regular', 'quiz'),
    'broadcast_validation.validate_attrs.5': ('tg-emoji', 'emoji-id'),
    'billing.create_platega_payment.1': ('merchant_id', 'secret'),
    'page_context.copy_visibility.1': ('visibility',),
    'page_context.copy_visibility.2': ('visibility', 'button_id'),
    'page_context.copy_visibility.3': ('visibility',),
    'page_flow.require_visibility_dict.1': ('visibility',),
    'page_flow.require_visibility_dict.2': ('visibility',),
    'payment_provider_registry.normalize_status.1': ('status', 'succeeded', 'pending', 'canceled'),
    'payment_provider_registry.normalize_status.2': ('status', 'succeeded', 'pending', 'canceled'),
    'db_payment_providers.normalize_status.1': ('status', 'succeeded', 'pending', 'canceled'),
}


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate catalog key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)


def variants(message):
    if isinstance(message, str):
        return [message]
    if (not isinstance(message, dict) or 'other' not in message
            or set(message) - {'zero', 'one', 'two', 'few', 'many', 'other'}
            or any(not isinstance(value, str) for value in message.values())):
        raise ValueError('Invalid message/plural forms')
    return list(message.values())


class Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.valid = [], True
    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.valid = False


def balanced(value):
    parser = Balance()
    parser.feed(''.join(TAGS.findall(value)))
    return parser.valid and not parser.stack


class FormattingContent(HTMLParser):
    """Count empty formatting spans, including whitespace-only nested spans."""

    def __init__(self):
        super().__init__()
        self.stack, self.empty = [], Counter()

    def handle_starttag(self, tag, attrs):
        if TAGS.fullmatch('<' + tag + '>'):
            self.stack.append([tag, False])

    def handle_data(self, data):
        if data.strip():
            for item in self.stack:
                item[1] = True

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            name, filled = self.stack.pop()
            if not filled:
                self.empty[name] += 1


def empty_formatting(value):
    parser = FormattingContent()
    parser.feed(value)
    return parser.empty


def preview_markup(value):
    """Keep the partial iframe template intact while allowing its two text nodes."""
    return re.sub(r'(?s)(<(title|p)>).*?(</\2>)', r'\1\3', value)


def admin_buttons():
    keys = set()
    for path in (ROOT / 'bot').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'InlineKeyboardButton':
                continue
            for keyword in node.keywords:
                if keyword.arg != 'text':
                    continue
                for call in ast.walk(keyword.value):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == '_tr'
                            and call.args and isinstance(call.args[0], ast.Constant)):
                        keys.add(call.args[0].value)
    return keys


def source_references():
    """Find static catalog references without importing the application or configuration."""
    for folder in ('bot', 'database', 'core', 'web_api', 'web_tools'):
        for path in (ROOT / folder).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for call in ast.walk(tree):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == '_tr' and call.args
                        and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    yield 'core', path.relative_to(ROOT).as_posix(), call.args[0].value
    for namespace, folder in (('application', 'web/src'), ('system', 'web/platform')):
        for path in (ROOT / folder).rglob('*'):
            if path.suffix not in {'.ts', '.tsx'}:
                continue
            for key in re.findall(r'\b_tr\([\s]*[\'"]([^\'"]+)[\'"]', path.read_text(encoding='utf-8')):
                yield namespace, path.relative_to(ROOT).as_posix(), key


def validate():
    languages = read(ROOT / 'locales/languages.json')
    expected = {'ru', 'en', 'fa', 'ar', 'es', 'id', 'ja', 'pt', 'sk', 'tr', 'vi', 'zh-CN', 'zh-TW'}
    if set(languages) != expected:
        raise ValueError('The release must include the approved 13 languages')
    sources = [('core', 'locales/{code}/admin.json'), ('seed', 'locales/{code}/seed.json'),
               ('installer', 'locales/{code}/installer.json'), ('application', 'web/src/i18n/locales/{code}.json'),
               ('system', 'web/platform/i18n/locales/{code}.json')]
    buttons = admin_buttons()
    ui_buttons = {'ui.' + item['key'] for item in read(ROOT / 'locales/user-fragments.json') if item['format'] == 'button'}
    issues, messages = [], 0
    baselines = {namespace: read(ROOT / pattern.format(code='ru')) for namespace, pattern in sources}
    for namespace, path, key in source_references():
        if key not in baselines[namespace]:
            issues.append(f'{path}: missing {namespace} key {key}')
    for namespace, pattern in sources:
        baseline = baselines[namespace]
        for code in languages:
            path = ROOT / pattern.format(code=code)
            if not path.is_file():
                issues.append(str(path.relative_to(ROOT)) + ': missing')
                continue
            data = read(path)
            if set(data) != set(baseline):
                issues.append(f'{namespace}/{code}: key mismatch missing={sorted(set(baseline)-set(data))}, extra={sorted(set(data)-set(baseline))}')
            for key, value in data.items():
                if key not in baseline:
                    continue
                messages += 1
                reference = variants(baseline[key])[0]
                for target in variants(value):
                    label = f'{namespace}/{code}/{key}'
                    if Counter(TOKENS.findall(target)) != Counter(TOKENS.findall(reference)):
                        issues.append(label + ': placeholders changed')
                    literals = INSTALLER_LITERALS if namespace == 'installer' else CORE_LITERALS
                    if namespace == 'core':
                        literals += CORE_KEY_LITERALS.get(key, ())
                    if namespace == 'installer' and ', '.join(INSTALLER_ACTIONS) in reference:
                        literals += INSTALLER_ACTIONS
                    for literal in literals:
                        if literal in reference and literal not in target:
                            issues.append(label + ': technical literal changed: ' + literal)
                    if namespace == 'core' and key.startswith('yadreno_admin.extract_chat_attachment_context.'):
                        technical = r"(?m)^- [a-z_]+: [^\n]+$|pages\.[a-z_]+(?:='[a-z_]+')?"
                        if Counter(re.findall(technical, target)) != Counter(re.findall(technical, reference)):
                            issues.append(label + ': attachment context fields changed')
                    if namespace == 'core' and key == 'preview.preview.2':
                        if preview_markup(target) != preview_markup(reference):
                            issues.append(label + ': preview HTML template changed')
                    if namespace == 'core' and re.search(r'\{(?:p[0-9]+|count|client|confirmation)\}', reference):
                        try:
                            fields = {name for _, name, _, _ in Formatter().parse(reference) if name is not None}
                            target.format_map({name: '123' for name in fields})
                        except (ValueError, KeyError, IndexError):
                            issues.append(label + ': invalid format template')
                    if Counter(TAGS.findall(target)) != Counter(TAGS.findall(reference)):
                        issues.append(label + ': HTML changed')
                    if balanced(reference) and not balanced(target):
                        issues.append(label + ': invalid HTML nesting')
                    if empty_formatting(target) - empty_formatting(reference):
                        issues.append(label + ': translated text lost inside HTML formatting')
                    if re.search(r'ZX[QM]\s*\d+|\d+\s*[QM]XZ', target, re.I):
                        issues.append(label + ': preparation marker leaked')
                    button = namespace == 'core' and key in buttons or namespace == 'seed' and ('.button.' in key or key in ui_buttons)
                    if button and (len(target) > 64 or '\n' in target or '\r' in target):
                        issues.append(label + ': button exceeds 64 characters or has line breaks')
                    if namespace == 'seed' and key.startswith('ui.command.') and not 1 <= len(target) <= 256:
                        issues.append(label + ': command description exceeds Telegram limits')
            if namespace == 'seed':
                names = [value for key, value in data.items() if key.startswith('account_name.')]
                if len(names) < 3 or len(set(names)) != len(names) or any(not value.strip() for value in names):
                    issues.append('seed/' + code + ': invalid default account name pool')
    return messages, issues


def main():
    count, issues = validate()
    print('Checked', count, 'messages across 13 languages and 5 surfaces')
    for issue in issues:
        print(issue)
    if issues:
        raise SystemExit(1)
    from localization.generate_installer import START, END, generated
    source = (ROOT / 'install.sh').read_text(encoding='utf-8')
    if source[source.index(START):source.index(END) + len(END)] != generated():
        raise SystemExit('Regenerate embedded installer catalogs')


if __name__ == '__main__':
    main()
