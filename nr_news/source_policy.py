"""Operator-approved publisher/domain pairs for current Facebook news cards."""
from urllib.parse import urlparse
from typing import Any

ALLOWED_NEWS_DOMAINS = {
    'the_hindu': 'thehindu.com',
    'india_today': 'indiatoday.in',
    'the_wire': 'thewire.in',
}


def source_allowed(candidate: dict[str, Any]) -> bool:
    try:
        expected = ALLOWED_NEWS_DOMAINS.get(candidate.get('source'))
        host = (urlparse(candidate.get('url') or '').hostname or '').lower()
        return bool(expected and (host == expected or host.endswith('.' + expected)))
    except (ValueError, TypeError):
        return False
