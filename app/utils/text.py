from __future__ import annotations

import html


def decode_html_entities(text: str | None) -> str | None:
    if text is None:
        return None
    return html.unescape(text)


def escape_sql_like_wildcards(value: str, escape_char: str = "\\") -> str:
    escaped = value.replace(escape_char, escape_char * 2)
    escaped = escaped.replace("%", f"{escape_char}%")
    escaped = escaped.replace("_", f"{escape_char}_")
    return escaped

