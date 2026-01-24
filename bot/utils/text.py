def escape_md(text: str) -> str:
    """
    Экранирует символы Markdown (v1) для Telegram.
    Экранируются: _ * ` [ ]
    """
    if not text:
        return ""
    # Порядок важен, чтобы не экранировать уже экранированное (хотя тут простые замены)
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def escape_md2(text: str) -> str:
    """
    Экранирует символы MarkdownV2 для Telegram.
    Экранируются: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    if not text:
        return ""
    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars:
        text = text.replace(char, '\\' + char)
    return text
