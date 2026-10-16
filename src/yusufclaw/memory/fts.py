"""Full-text search query sanitization and utilities."""

import re

# Match Unicode alphanumeric words and underscores, matching TS /[\p{L}\p{N}_]+/gu
_TOKEN_RE = re.compile(r"[\w]+", flags=re.UNICODE)


def sanitize_fts5_query(query: str) -> str:
    """Sanitize a raw user query string for SQLite FTS5 prefix match syntax.

    Removes special operators and punctuation that would trigger FTS5 syntax errors,
    supports international characters (e.g. Turkish characters like ç, ğ, ı, ö, ş, ü),
    and wraps each extracted token in prefix-match quotes: '"token"*'.
    """
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t.replace('"', "")}"*' for t in tokens)


def sanitize_tsquery(query: str) -> str:
    """Sanitize a raw user query string for PostgreSQL plainto_tsquery or to_tsquery."""
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return ""
    return " & ".join(tokens)


__all__ = ["sanitize_fts5_query", "sanitize_tsquery"]
