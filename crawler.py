"""Chạy riêng: python crawler.py (mỗi 15 phút), hoặc python crawler.py --once."""
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from filelock import FileLock, Timeout
from article_parser import canonical_url, listing_links, parse_article
from full_loop import STATE_DIR, ensure_full_index, index_article

LOG = logging.getLogger('crawler')
SOURCES = {'VnExpress': 'https://vnexpress.net/rss/tin-moi-nhat.rss',
           'Báo Mới': 'https://baomoi.com/tin-moi.epi'}
INTERVAL_SECONDS = 15 * 60


def baomoi_content_id(url):
    match = re.search(r'-c(\d+)\.epi$', urlsplit(url).path)
    return int(match.group(1)) if match else None


def find_baomoi_object(value, content_id):
    """Tìm object bài trong JSON lồng nhau của Báo Mới."""
    if isinstance(value, dict):
        object_id = next((value[key] for key in
                          ('id', 'contentId', 'content_id', 'contentID')
                          if key in value), None)
        try:
            matches = int(object_id) == content_id
        except (TypeError, ValueError):
            matches = False
        if matches and isinstance(value.get('title'), str):
            return value
        for child in value.values():
            found = find_baomoi_object(child, content_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_baomoi_object(child, content_id)
            if found:
                return found
    return None


def baomoi_image_urls(value, content_id):
    """Lấy URL ảnh từ object API, bỏ URL bài và tài nguyên không phải ảnh."""
    urls = []

    def visit(node, key=''):
        if isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, str(child_key).casefold())
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str):
            candidate = node.strip()
            looks_like_image = ('image' in key or 'thumb' in key or 'avatar' in key or
                                'photo' in key or 'photo-baomoi' in candidate or
                                re.search(r'\.(?:avif|webp|jpe?g|png)(?:\?|$)', candidate, re.I))
            parts = urlsplit(candidate)
            # Chỉ nhận ảnh Baomoi CDN có content ID của đúng bài. Việc này bỏ
            # logo/avatar dùng chung và ảnh thumbnail của các bài liên quan.
            if (looks_like_image and parts.scheme in ('http', 'https') and
                    parts.hostname == 'photo-baomoi.bmcdn.me' and
                    f'_{content_id}/' in parts.path):
                path = re.sub(r'/w\d+_r[^/]+(?=/)', '', parts.path)
                identity = (parts.hostname, path)
                if identity not in seen:
                    seen.add(identity)
                    urls.append(candidate)

    seen = set()
    visit(value)
    return urls


def article_from_baomoi_json(payload, url, collection='Báo Mới'):
    """Đổi JSON API/card thành article và subdocument ảnh có mô tả đi kèm."""
    item = find_baomoi_object(payload, baomoi_content_id(url))
    if not item:
        return None
    title = ' '.join(item.get('title', '').split())
    description = ' '.join(str(item.get('description') or item.get('summary') or '').split())
    if not title or not description:
        return None
    publisher = item.get('publisher')
    publisher = publisher.get('name') if isinstance(publisher, dict) else publisher
    raw_date = item.get('date', item.get('publishedAt', ''))
    if isinstance(raw_date, (int, float)):
        published = datetime.fromtimestamp(raw_date, timezone.utc).isoformat()
    else:
        published = str(raw_date or '')
    images = [{'image_url': image_url, 'description': description}
              for image_url in baomoi_image_urls(item, baomoi_content_id(url))]
    return dict(url=canonical_url(url), title=title, description=description,
                collection=collection, publisher=str(publisher or collection),
                published=published, images=images)


class HybridFetcher:
    """HTTP cho trang tĩnh; dùng một Firefox dùng lại được cho bài Báo Mới."""

    def __init__(self):
        self.playwright = self.browser = self.page = None

    def _ensure_browser(self):
        if self.browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                'Báo Mới cần Playwright. Chạy: pip install -r requirements.txt '
                'và python -m playwright install firefox') from exc
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.firefox.launch(headless=True)

    def __call__(self, url):
        parts = urlsplit(url)
        is_baomoi_article = (parts.hostname == 'baomoi.com' and
                             re.search(r'-c\d+\.epi$', parts.path))
        if not is_baomoi_article:
            return fetch(url)
        self._ensure_browser()
        if self.page is None:
            self.page = self.browser.new_page(
                viewport={'width': 1280, 'height': 900},
                user_agent=('Mozilla/5.0 (X11; Linux x86_64; rv:128.0) '
                            'Gecko/20100101 Firefox/128.0'))
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=60_000)
        if response is not None and response.status >= 400:
            raise RuntimeError(f'HTTP {response.status}')
        # Nội dung Báo Mới được gắn vào DOM bằng JavaScript. Chờ một trong các
        # dấu hiệu bài đã render; timeout ngắn vẫn trả DOM để parser báo rõ lỗi.
        try:
            self.page.wait_for_selector(
                'h1, article, .xeditor_content, .app_h5_article, .content-detail',
                timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(1_000)
        return self.page.content()

    def fetch_article(self, url, collection):
        """Báo Mới ưu tiên JSON mà trang dùng; nguồn khác dùng HTML."""
        if collection != 'Báo Mới':
            return parse_article(self(url), url, collection)
        self._ensure_browser()
        page = self.browser.new_page(
            viewport={'width': 1280, 'height': 900},
            user_agent=('Mozilla/5.0 (X11; Linux x86_64; rv:128.0) '
                        'Gecko/20100101 Firefox/128.0'))
        responses = []
        page.on('response', lambda response: responses.append(response)
                if urlsplit(response.url).hostname in
                ('w-api.baomoi.com', 'api.baomoi.com') else None)
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=60_000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f'HTTP {response.status}')
            page.wait_for_timeout(2_000)
            payloads = []
            for api_response in responses:
                try:
                    if api_response.ok:
                        payloads.append(api_response.json())
                except Exception:
                    continue
            scripts = page.locator(
                'script[type="application/json"], script#__NEXT_DATA__').all_text_contents()
            for text in scripts:
                try:
                    payloads.append(json.loads(text))
                except (TypeError, json.JSONDecodeError):
                    continue
            for payload in payloads:
                article = article_from_baomoi_json(payload, url, collection)
                if article:
                    return article
            try:
                return parse_article(page.content(), url, collection)
            except ValueError as exc:
                api_paths = sorted({
                    urlsplit(api_response.url).path for api_response in responses
                })
                raise ValueError(
                    f'{exc} Báo Mới contentId={baomoi_content_id(url)}; '
                    f'đã nhận {len(responses)} response API, không tìm thấy JSON '
                    f'bài phù hợp; endpoints={api_paths or "none"}.'
                ) from exc
        finally:
            page.close()

    def close(self):
        if self.browser is not None:
            self.browser.close()
        if self.playwright is not None:
            self.playwright.stop()
        self.playwright = self.browser = self.page = None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url):
    request = Request(url, headers={'User-Agent': 'IR-Coursework/1.0', 'Accept': 'text/html,application/xml'})
    with urlopen(request, timeout=30) as response:
        # Giữ UTF-8 tiếng Việt, không dựa vào mặc định ISO-8859-1.
        return response.read().decode(response.headers.get_content_charset() or 'utf-8', errors='replace')


def discover(source, fetcher=fetch):
    html = fetcher(SOURCES[source])
    if source == 'Báo Mới':
        urls = listing_links(html, SOURCES[source])
    else:
        root = ET.fromstring(html)
        urls = list(dict.fromkeys(canonical_url(n.text or '') for n in root.findall('.//item/link')))
        urls = [u for u in urls if urlsplit(u).hostname == 'vnexpress.net'
                and re.search(r'-\d+\.html$', urlsplit(u).path)]
    if not urls:
        raise ValueError(f'{source}: không tìm thấy URL bài; kiểm tra phản hồi/selector.')
    return urls


def open_database(state_dir=STATE_DIR):
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(state_dir) / 'crawl.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS articles (
        url TEXT PRIMARY KEY, collection TEXT NOT NULL, payload TEXT,
        indexed INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, fetched_at TEXT, retry_at REAL NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report TEXT)''')
    db.commit()
    return db


def sync_pending(db, ix, state_dir):
    # SQLite và Whoosh không có transaction chung. Ghi payload trước, commit
    # index sau, cuối cùng mới đánh dấu indexed=1. Crash ở giữa sẽ được replay.
    rows = db.execute('SELECT url,payload FROM articles WHERE payload IS NOT NULL AND indexed=0').fetchall()
    for row in rows:
        index_article(ix, json.loads(row['payload']), state_dir)
        db.execute('UPDATE articles SET indexed=1 WHERE url=?', (row['url'],))
        db.commit()
    return len(rows)


def run_cycle(state_dir=STATE_DIR, sources=None, fetcher=None, delay=1.0, max_articles=0, datasets=None):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    owned_fetcher = fetcher is None
    fetcher = HybridFetcher() if owned_fetcher else fetcher
    # Cả lệnh --once và vòng lặp dùng cùng khóa, không có hai crawler cùng tải bài.
    with FileLock(str(state_dir / 'crawler.lock'), timeout=0):
        ix = ensure_full_index(state_dir, **({'datasets': datasets} if datasets is not None else {}))
        db = open_database(state_dir)
        report = dict(discovered=0, fetched=0, indexed=0, failures=0, errors=[])
        run_id = db.execute('INSERT INTO runs(started_at) VALUES (?)', (utc_now(),)).lastrowid
        db.commit()
        try:
            report['indexed'] += sync_pending(db, ix, state_dir)
            for source in sources or SOURCES:
                try:
                    urls = discover(source, fetcher)
                    for url in urls:
                        cursor = db.execute('INSERT OR IGNORE INTO articles(url,collection) VALUES (?,?)', (url, source))
                        report['discovered'] += cursor.rowcount
                    db.commit()
                except Exception as exc:
                    report['errors'].append(f'{source}: {type(exc).__name__}: {exc}')
                    LOG.warning('%s', report['errors'][-1])
            # Chỉ tải bài chưa có payload; bài đã thành công không được tải lại.
            selected = list(sources or SOURCES)
            placeholders = ','.join('?' for _ in selected)
            rows = db.execute(f'''SELECT * FROM articles WHERE payload IS NULL AND retry_at<=?
                AND collection IN ({placeholders}) ORDER BY attempts, rowid LIMIT ?''',
                [time.time(), *selected, max_articles or -1]).fetchall()
            for row in rows:
                try:
                    if hasattr(fetcher, 'fetch_article'):
                        article = fetcher.fetch_article(row['url'], row['collection'])
                    else:
                        article = parse_article(fetcher(row['url']), row['url'], row['collection'])
                    db.execute('UPDATE articles SET payload=?, fetched_at=?, last_error=NULL WHERE url=?',
                               (json.dumps(article, ensure_ascii=False), utc_now(), row['url']))
                    db.commit()
                    report['fetched'] += 1
                except Exception as exc:
                    # Bài lỗi vẫn ở hàng đợi; tăng thời gian chờ, tối đa 24 giờ.
                    wait = min(INTERVAL_SECONDS * 2 ** min(row['attempts'], 7), 86400)
                    db.execute('UPDATE articles SET attempts=attempts+1,last_error=?,retry_at=? WHERE url=?',
                               (f'{type(exc).__name__}: {exc}', time.time() + wait, row['url']))
                    db.commit()
                    report['failures'] += 1
                    LOG.warning('ARTICLE %s: %s', row['url'], exc)
                # Nếu ghi index lỗi, payload vẫn còn để lần sau thử lại không tải HTML.
                report['indexed'] += sync_pending(db, ix, state_dir)
                if delay:
                    time.sleep(delay)
        except BaseException as exc:
            report['errors'].append(f'{type(exc).__name__}: {exc}')
            raise
        finally:
            db.execute('UPDATE runs SET finished_at=?,report=? WHERE id=?',
                       (utc_now(), json.dumps(report, ensure_ascii=False), run_id))
            db.commit()
            db.close()
            ix.close()
            if owned_fetcher:
                fetcher.close()
        return report


def crawl_status(state_dir=STATE_DIR):
    path = Path(state_dir) / 'crawl.sqlite'
    if not path.exists():
        return None
    db = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        latest = db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        counts = db.execute('''SELECT count(*) AS discovered,
            sum(payload IS NOT NULL) AS fetched, sum(indexed) AS indexed,
            sum(payload IS NULL) AS pending FROM articles''').fetchone()
        return dict(counts=dict(counts), latest=dict(latest) if latest else None)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Chạy một lượt rồi thoát')
    parser.add_argument('--source', choices=['all', *SOURCES], default='all')
    parser.add_argument('--max-articles', type=int, default=0, help='0 = toàn bộ hàng đợi; số dương để chạy thử')
    args = parser.parse_args()
    if args.max_articles < 0:
        parser.error('--max-articles phải >= 0')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    sources = list(SOURCES) if args.source == 'all' else [args.source]
    try:
        while True:
            started = time.monotonic()
            try:
                report = run_cycle(sources=sources, max_articles=args.max_articles)
                LOG.info('CYCLE %s', json.dumps(report, ensure_ascii=False))
                if args.once and (report['errors'] or report['failures']):
                    raise SystemExit(1)
            except Timeout:
                LOG.error('Một crawler khác đang chạy. Không khởi động bản trùng.')
                raise SystemExit(1)
            except Exception:
                LOG.exception('Lượt crawl lỗi; tiến độ đã lưu, lượt sau sẽ tiếp tục.')
                if args.once:
                    raise SystemExit(1)
            if args.once:
                break
            # Chu kỳ tính từ đầu lượt; lượt dài hơn 15 phút không chạy chồng nhau.
            remaining = max(0, INTERVAL_SECONDS - (time.monotonic() - started))
            LOG.info('Kiểm tra tiếp sau %.0f giây. Ctrl+C để dừng.', remaining)
            time.sleep(remaining)
    except KeyboardInterrupt:
        LOG.info('Đã dừng. Lần chạy sau tiếp tục từ SQLite.')


if __name__ == '__main__':
    main()
