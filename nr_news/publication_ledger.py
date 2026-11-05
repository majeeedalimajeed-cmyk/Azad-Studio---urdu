"""Durable write-before-upload Facebook ledger on a dedicated GitHub branch.

One JSON document is updated with GitHub's blob-SHA compare-and-swap. A
reservation is made before the Graph API call; every reservation, including
one whose upload result is unknown, permanently blocks automatic re-upload.
A missing, unreadable, corrupt, or unwritable ledger always fails closed.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import requests

from .util import canonical_url, iso, parse_iso, similarity, tokens, utcnow


class LedgerUnavailable(RuntimeError):
    pass


class LedgerDuplicate(RuntimeError):
    pass


class GitHubPublicationLedger:
    def __init__(self, *, token: str | None = None, repo: str | None = None,
                 branch: str = 'publishing-ledger', session: Any = None):
        self.token = token if token is not None else os.getenv('NR_GITHUB_LEDGER_TOKEN', '')
        self.repo = repo if repo is not None else os.getenv('GITHUB_REPOSITORY', '')
        self.branch = branch
        self.session = session or requests
        self.url = f'https://api.github.com/repos/{self.repo}/contents/publication-ledger.json'

    def _request(self, method: str, **kwargs: Any) -> Any:
        if not self.token or '/' not in self.repo:
            raise LedgerUnavailable('Durable ledger credentials or repository are not configured')
        try:
            return self.session.request(method, self.url, headers={
                'Authorization': f'Bearer {self.token}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
            }, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise LedgerUnavailable(f'Durable ledger network error: {type(exc).__name__}') from exc

    def _read(self) -> tuple[dict[str, Any], str]:
        response = self._request('GET', params={'ref': self.branch})
        if response.status_code != 200:
            raise LedgerUnavailable(f'Durable ledger read failed: HTTP {response.status_code}')
        try:
            payload = response.json()
            sha = payload['sha']
            content = payload.get('content') or ''
            if payload.get('encoding') != 'base64' or not content:
                # The Contents API stops returning inline bodies above 1 MB.
                blob_url = f'https://api.github.com/repos/{self.repo}/git/blobs/{quote(sha)}'
                blob = self.session.request('GET', blob_url, headers={
                    'Authorization': f'Bearer {self.token}',
                    'Accept': 'application/vnd.github+json',
                    'X-GitHub-Api-Version': '2022-11-28',
                }, timeout=30)
                if blob.status_code != 200:
                    raise LedgerUnavailable(f'Durable ledger blob read failed: HTTP {blob.status_code}')
                bp = blob.json()
                if bp.get('encoding') != 'base64':
                    raise ValueError('unexpected blob encoding')
                content = bp['content']
            data = json.loads(base64.b64decode(content).decode('utf-8'))
            if not isinstance(sha, str) or not sha or not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('records'), list):
                raise ValueError('invalid ledger schema')
            for row in data['records']:
                if (not isinstance(row, dict) or not row.get('key') or
                    row.get('status') not in {'reserved', 'confirmed', 'uncertain', 'rejected'} or
                    not row.get('url') or not row.get('headline') or not parse_iso(row.get('created_at'))):
                    raise ValueError('invalid ledger record')
                nested = row.get('stories', [])
                if not isinstance(nested, list) or any(not isinstance(x, dict) or not x.get('url') or not x.get('headline') or 'stories' in x for x in nested):
                    raise ValueError('invalid card story records')
            return data, sha
        except LedgerUnavailable:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, requests.RequestException) as exc:
            raise LedgerUnavailable('Durable ledger is unreadable or corrupt') from exc

    def _write(self, data: dict[str, Any], sha: str, message: str) -> bool:
        response = self._request('PUT', json={
            'message': message,
            'branch': self.branch,
            'sha': sha,
            'content': base64.b64encode(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).decode(),
        })
        if response.status_code in (409, 422):
            return False  # A concurrent writer won the compare-and-swap.
        if response.status_code != 200:
            raise LedgerUnavailable(f'Durable ledger write failed: HTTP {response.status_code}')
        return True

    @staticmethod
    def _duplicate(record: dict[str, Any], candidate: dict[str, Any]) -> bool:
        for item in record.get('stories', []):
            if GitHubPublicationLedger._duplicate({**item, 'created_at': record.get('created_at')}, candidate):
                return True
        url = candidate.get('canonical_url') or candidate.get('url') or ''
        old_url = record.get('url') or ''
        if url and old_url and canonical_url(url) == canonical_url(old_url):
            return True
        headline = str(candidate.get('original_headline') or '').strip()
        old_title = str(record.get('headline') or '').strip()
        when = parse_iso(record.get('created_at'))
        if not headline or not old_title or not when or when < utcnow() - timedelta(hours=72):
            return False
        common = {t for t in tokens(headline) & tokens(old_title) if len(t) >= 4}
        return len(common) >= 2 and similarity(headline, old_title) >= 0.68

    def reserve(self, *, key: str, candidate: dict[str, Any], editorial_id: str,
                page_id: str, caption_marker: str) -> None:
        self.reserve_many(key=key, candidates=[candidate], editorial_id=editorial_id,
                          page_id=page_id, caption_marker=caption_marker)

    def reserve_many(self, *, key: str, candidates: list[dict[str, Any]], editorial_id: str,
                     page_id: str, caption_marker: str) -> None:
        """Atomically reserve every story on a single card before upload."""
        if not candidates or not key or not page_id:
            raise LedgerUnavailable('Durable ledger requires stories, key and Page ID')
        stories = []
        for candidate in candidates:
            url = candidate.get('canonical_url') or candidate.get('url')
            title = candidate.get('original_headline')
            if not url or not title:
                raise LedgerUnavailable('Each card story needs a source URL and headline')
            stories.append({'url': canonical_url(url), 'headline': str(title)[:500]})
        for idx, story in enumerate(stories):
            for earlier in stories[:idx]:
                if self._duplicate({**earlier, 'created_at': iso()}, {'url': story['url'], 'original_headline': story['headline']}):
                    raise LedgerDuplicate('A card cannot contain duplicate stories')
        for _ in range(4):
            data, sha = self._read()
            for previous in data['records']:
                if previous['key'] == key or any(self._duplicate(previous, c) for c in candidates):
                    raise LedgerDuplicate('A matching story already has a durable upload claim')
            data['records'].append({
                'key': key, 'status': 'reserved', 'created_at': iso(),
                'url': stories[0]['url'], 'headline': stories[0]['headline'],
                'stories': stories, 'editorial_id': editorial_id, 'page_id': page_id,
                'caption_marker': caption_marker,
            })
            if self._write(data, sha, 'Reserve all card stories before upload'):
                return
        raise LedgerUnavailable('Durable ledger changed repeatedly; upload held')

    def finish(self, key: str, *, status: str, post_id: str = '') -> None:
        if status not in {'confirmed', 'uncertain', 'rejected'}:
            raise LedgerUnavailable('Invalid ledger outcome')
        for _ in range(4):
            data, sha = self._read()
            match = next((x for x in data['records'] if x['key'] == key), None)
            if not match:
                raise LedgerUnavailable('Publication reservation disappeared')
            match['status'] = status
            match['updated_at'] = iso()
            if post_id:
                match['post_id'] = post_id
            if self._write(data, sha, 'Record Facebook upload outcome'):
                return
        raise LedgerUnavailable('Unable to save publication outcome')

    def probe(self) -> None:
        """Verify read/write permission without reserving or posting a story."""
        for _ in range(4):
            data, sha = self._read()
            data['last_write_probe'] = {'at': iso(), 'run_id': os.getenv('GITHUB_RUN_ID', 'manual')}
            if self._write(data, sha, 'Verify publishing ledger write access'):
                return
        raise LedgerUnavailable('Unable to verify ledger write access')


if __name__ == '__main__':
    GitHubPublicationLedger().probe()
    print('Durable publication ledger: read and compare-and-swap write verified; no post sent.')
