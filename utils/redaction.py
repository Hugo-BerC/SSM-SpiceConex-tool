"""Redact known credential fields and authorization URLs from diagnostics."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_KEYS = (r"aws[_-]?access[_-]?key[_-]?id|access[_-]?key[_-]?id|"
         r"(?:aws[_-]?)?secret[_-]?access[_-]?key|(?:aws[_-]?)?session[_-]?token|"
         r"access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
         r"password|passwd|authorization|token|user[_-]?code|code[_-]?challenge|"
         r"code[_-]?verifier|request[_-]?uri|samlresponse|x-amz-(?:security-token|signature|credential)")
_QUOTED = re.compile(r'(?P<prefix>["\']?\b(?:' + _KEYS + r')["\']?\s*[:=]\s*)(?P<quote>["\'])(?:\\.|(?!(?P=quote)).)*(?P=quote)', re.I | re.S)
_PLAIN = re.compile(r'(?P<prefix>\b(?:' + _KEYS + r')\s*[:=]\s*)(?:\[REDACTED\]|[^\s,;\}\]\"\']+)', re.I)
_CLI = re.compile(r'(?P<prefix>--(?:' + _KEYS + r')\s+)(?:"[^"]*"|\'[^\']*\'|\S+)', re.I)
_AUTH = re.compile(r'(?P<prefix>\bauthorization\s*[:=]\s*)(?:Bearer|Basic)\s+\S+', re.I)
_ACCESS_KEY = re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')
_URL = re.compile(r'https?://[^\s<>"\']+', re.I)
_SECRET_QUERY = re.compile(r'^(?:' + _KEYS + r'|code|state)$', re.I)


def _redact_url(match):
    value = match.group(0)
    try:
        parts = urlsplit(value)
        if any(marker in value.lower() for marker in ("/authorize", "/device", "user_code=", "code_challenge=")):
            return "[REDACTED_AUTH_URL]"
        authority = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
        query = parse_qsl(parts.query, keep_blank_values=True)
        if parts.username or any(_SECRET_QUERY.fullmatch(key) for key, _ in query):
            query = [(key, "[REDACTED]" if _SECRET_QUERY.fullmatch(key) else item) for key, item in query]
            return urlunsplit(parts._replace(netloc=authority, query=urlencode(query)))
    except ValueError:
        return "[REDACTED_URL]"
    return value


def redact_sensitive(value: object) -> str:
    text = str(value)
    text = _URL.sub(_redact_url, text)
    text = _AUTH.sub(lambda match: match["prefix"] + "[REDACTED]", text)
    text = _QUOTED.sub(lambda match: match["prefix"] + match["quote"] + "[REDACTED]" + match["quote"], text)
    text = _PLAIN.sub(lambda match: match["prefix"] + "[REDACTED]", text)
    text = _CLI.sub(lambda match: match["prefix"] + "[REDACTED]", text)
    return _JWT.sub("[REDACTED]", _ACCESS_KEY.sub("[REDACTED_ACCESS_KEY]", text))
