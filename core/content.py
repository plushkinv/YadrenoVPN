"""Small inert HTML subset for browser projections of stored Telegram content."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


def safe_url(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == 'https' and parsed.hostname and parsed.username is None and parsed.password is None:
            return value
    except ValueError:
        pass
    return None


class _SafeHTML(HTMLParser):
    tags = {'b', 'strong', 'i', 'em', 'u', 's', 'del', 'code', 'pre', 'blockquote', 'a'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.output.append('<br>')
        elif tag in self.tags:
            attribute = ''
            if tag == 'a':
                url = safe_url(dict(attrs).get('href'))
                if url is None:
                    return
                attribute = ' href="' + escape(url, quote=True) + '" rel="noopener noreferrer"'
            self.output.append('<' + tag + attribute + '>')
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append('</' + current + '>')
                if current == tag:
                    break

    def handle_data(self, data):
        self.output.append(escape(data, quote=False))


def safe_html(value):
    parser = _SafeHTML()
    parser.feed(str(value or ''))
    parser.close()
    while parser.stack:
        parser.output.append('</' + parser.stack.pop() + '>')
    return ''.join(parser.output)
