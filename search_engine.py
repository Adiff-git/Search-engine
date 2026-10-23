"""Persistent Whoosh indexing and searching for the Week 1 news crawl."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
from urllib.parse import urldefrag, urlparse

from filelock import FileLock
from whoosh import index
from whoosh.analysis import LowercaseFilter, RegexTokenizer
from whoosh.fields import ID, TEXT, Schema
from whoosh.qparser import AndGroup, MultifieldParser, OrGroup
from whoosh.query import Term

ROOT = Path(__file__).resolve().parent
DATASETS = (
    (ROOT / 'baomoi_24h.json', 'Báo Mới'),
    (ROOT / 'vnexpress_24h.json', 'VnExpress'),
)
INDEX_DIR = ROOT / '.whoosh_index'


def normalize(text):
    return unicodedata.normalize('NFC', str(text or ''))


def load_documents(datasets):
    """Use only crawled titles/descriptions; discard duplicate article URLs."""
    seen = set()
    for path, collection in datasets:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        for article in payload['articles']:
            url = urldefrag(str(article.get('url') or ''))[0]
            title = normalize(article.get('title')).strip()
            if not title or urlparse(url).scheme not in ('http', 'https') or url in seen:
                continue
            seen.add(url)
            yield dict(
                url=url, title=title,
                description=normalize(article.get('description')),
                publisher=normalize(article.get('publisher') or collection),
                collection=collection,
                published=normalize(article.get('date_utc') or article.get('published_at')),
            )


def ensure_index(index_dir=INDEX_DIR, datasets=DATASETS):
    """Build once, then reopen. A lock serializes concurrent first launches.

    Build in a temporary directory and publish only after commit, so an
    interrupted build cannot leave an empty index that looks complete.
    Existing indexes are never refreshed implicitly, even if JSON changes.
    """
    index_dir = Path(index_dir)
    index_dir.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(index_dir) + '.lock', timeout=120):
        if index.exists_in(str(index_dir)):
            return index.open_dir(str(index_dir))
        if index_dir.exists():
            raise RuntimeError(f'Incomplete index at {index_dir}. Remove it and restart.')
        # No English stopword removal or stemming: the crawl is Vietnamese.
        analyzer = RegexTokenizer() | LowercaseFilter()
        schema = Schema(
            url=ID(stored=True, unique=True),
            title=TEXT(stored=True, analyzer=analyzer),
            description=TEXT(stored=True, analyzer=analyzer),
            publisher=ID(stored=True), collection=ID(stored=True),
            published=ID(stored=True),
        )
        staging = Path(tempfile.mkdtemp(prefix='.whoosh-build-', dir=index_dir.parent))
        try:
            ix = index.create_in(str(staging), schema)
            writer = ix.writer()
            try:
                count = 0
                for document in load_documents(datasets):
                    writer.add_document(**document)
                    count += 1
                if not count:
                    raise ValueError('The datasets contain no usable articles.')
                writer.commit()
            except BaseException:
                writer.cancel()
                raise
            ix.close()
            os.replace(staging, index_dir)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return index.open_dir(str(index_dir))


def search_documents(ix, query, collection='All sources', match_all=True, page=1, page_size=10):
    """Return plain data so no live Whoosh hits escape the searcher context."""
    if not query.strip():
        return {'total': 0, 'pages': 0, 'page': 1, 'documents': []}
    parser = MultifieldParser(
        ['title', 'description'], schema=ix.schema,
        fieldboosts={'title': 2.0}, group=AndGroup if match_all else OrGroup,
    )
    parsed = parser.parse(normalize(query))
    source_filter = None if collection == 'All sources' else Term('collection', collection)
    # A fresh searcher per request avoids sharing mutable readers across sessions.
    with ix.searcher() as searcher:
        results = searcher.search(parsed, filter=source_filter, limit=None)
        total = len(results)
        pages = (total + page_size - 1) // page_size
        page = max(1, min(page, pages or 1))
        start = (page - 1) * page_size
        documents = [dict(hit.fields(), score=hit.score) for hit in results[start:start + page_size]]
        return {'total': total, 'pages': pages, 'page': page, 'documents': documents}
