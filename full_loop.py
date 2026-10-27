"""Index bài viết và subdocument ảnh. Không tạo lại index khi Streamlit rerun."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import re
from urllib.parse import urlsplit

from filelock import FileLock
from whoosh import index
from whoosh.analysis import RegexTokenizer, LowercaseFilter
from whoosh.fields import ID, TEXT, STORED, Schema
from whoosh.qparser import MultifieldParser, AndGroup, OrGroup, FieldsPlugin
from whoosh.query import And, Term
from search_engine import ROOT, DATASETS, load_documents, normalize

STATE_DIR = ROOT / '.full_loop'


def document_id(kind, url, image_url=''):
    return hashlib.sha256(f'{kind}\n{url}\n{image_url}'.encode()).hexdigest()


def image_identity(article_url, image_url, collection):
    """Khóa dedupe ảnh theo bài, bỏ rendition-size và asset Báo Mới dùng chung."""
    parts = urlsplit(image_url)
    if collection == 'Báo Mới':
        match = re.search(r'-c(\d+)\.epi$', urlsplit(article_url).path)
        if (not match or parts.hostname != 'photo-baomoi.bmcdn.me' or
                f'_{match.group(1)}/' not in parts.path):
            return None
    path = re.sub(r'/w\d+_r[^/]+(?=/)', '', parts.path)
    return article_url, parts.hostname, path


def ensure_full_index(state_dir=STATE_DIR, datasets=DATASETS):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = state_dir / 'index'
    with FileLock(str(state_dir / 'index.lock'), timeout=120):
        if index.exists_in(str(destination)):
            return index.open_dir(str(destination))
        if destination.exists():
            raise RuntimeError('Index chưa hoàn chỉnh: hãy kiểm tra thư mục .full_loop/index.')
        analyzer = RegexTokenizer() | LowercaseFilter()
        schema = Schema(uid=ID(unique=True, stored=True), kind=ID(stored=True),
                        url=ID(stored=True), image_url=STORED, title=TEXT(stored=True, analyzer=analyzer),
                        description=TEXT(stored=True, analyzer=analyzer),
                        collection=ID(stored=True), publisher=STORED, published=STORED)
        staging = Path(tempfile.mkdtemp(prefix='build-', dir=state_dir))
        try:
            ix = index.create_in(str(staging), schema)
            with ix.writer() as writer:
                # Nhập snapshot tuần 1 một lần. Các bài này chưa có dữ liệu ảnh.
                for doc in load_documents(datasets):
                    writer.add_document(uid=document_id('article', doc['url']), kind='article', **doc)
            ix.close()
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return index.open_dir(str(destination))


def index_article(ix, article, state_dir=STATE_DIR):
    """Commit một bài + các ảnh. Retry sau crash không sinh bản trùng."""
    with FileLock(str(Path(state_dir) / 'index.lock'), timeout=120):
        with ix.writer() as writer:
            # Xóa bản cũ theo URL, rồi thêm cùng một bộ tài liệu trong một commit.
            writer.delete_by_term('url', article['url'])
            common = {k: normalize(article.get(k)) for k in
                      ('url', 'title', 'collection', 'publisher', 'published')}
            writer.add_document(uid=document_id('article', article['url']), kind='article',
                                description=normalize(article['description']), **common)
            for image in article['images']:
                writer.add_document(uid=document_id('image', article['url'], image['image_url']),
                                    kind='image', image_url=image['image_url'],
                                    description=normalize(image['description']), **common)


def search_full(ix, query, collection='All sources', match_all=True, page=1, page_size=10, kind='article'):
    if not query.strip():
        return dict(total=0, pages=0, page=1, documents=[])
    fields = ['description'] if kind == 'image' else ['title', 'description']
    parser = MultifieldParser(fields, ix.schema, group=AndGroup if match_all else OrGroup)
    if kind == 'image':
        # Không cho title: vượt quy tắc đề bài: ảnh chỉ khớp văn bản ngay sau ảnh.
        parser.remove_plugin_class(FieldsPlugin)
    filters = [Term('kind', kind)]
    if collection != 'All sources':
        filters.append(Term('collection', collection))
    # Mỗi lượt mở searcher mới để đọc được commit mới nhất từ crawler.
    with ix.searcher() as searcher:
        hits = searcher.search(parser.parse(normalize(query)), filter=And(filters), limit=None)
        documents = []
        seen_images = set()
        for hit in hits:
            fields = hit.fields()
            if kind == 'image':
                identity = image_identity(fields['url'], fields['image_url'], fields['collection'])
                if identity is None or identity in seen_images:
                    continue
                seen_images.add(identity)
            fields['score'] = hit.score
            fields['title_highlight'] = hit.highlights('title', top=1) if 'title' in fields else ''
            fields['description_highlight'] = hit.highlights('description', top=2)
            documents.append(fields)
        total = len(documents)
        pages = (total + page_size - 1) // page_size
        page = max(1, min(page, pages or 1))
        start = (page - 1) * page_size
        return dict(total=total, pages=pages, page=page,
                    documents=documents[start:start + page_size])


def index_counts(ix):
    with ix.searcher() as searcher:
        article_count = len(searcher.search(Term('kind', 'article'), limit=None))
        image_keys = set()
        for hit in searcher.search(Term('kind', 'image'), limit=None):
            fields = hit.fields()
            key = image_identity(fields['url'], fields['image_url'], fields['collection'])
            if key is not None:
                image_keys.add(key)
        return {'article': article_count, 'image': len(image_keys)}
