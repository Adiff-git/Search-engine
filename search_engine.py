"""Persistent Whoosh indexing and searching for the Week 1 news crawl."""

# File này xử lý dữ liệu và tìm kiếm; không phụ thuộc vào giao diện Streamlit.
# json đọc dữ liệu crawl; pathlib thao tác đường dẫn; các import còn lại
# hỗ trợ chuẩn hóa chữ, xử lý URL và tạo index an toàn.
import json
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
from urllib.parse import urldefrag, urlparse

# FileLock ngăn hai tiến trình cùng tạo index vào một thời điểm.
from filelock import FileLock
from whoosh import index
from whoosh.analysis import LowercaseFilter, RegexTokenizer
from whoosh.fields import ID, TEXT, Schema
from whoosh.qparser import AndGroup, MultifieldParser, OrGroup
from whoosh.query import Term

# __file__ là đường dẫn file Python hiện tại; parent là thư mục chứa nó.
# Nhờ vậy, đường dẫn dữ liệu không phụ thuộc nơi bạn mở terminal.
ROOT = Path(__file__).resolve().parent
DATASETS = (
    (ROOT / 'baomoi_24h.json', 'Báo Mới'),
    (ROOT / 'vnexpress_24h.json', 'VnExpress'),
)
INDEX_DIR = ROOT / '.whoosh_index'


# NFC đưa những cách biểu diễn Unicode tương đương về cùng dạng.
# Không xóa dấu tiếng Việt; None hoặc chuỗi rỗng trở thành chuỗi rỗng.
def normalize(text):
    return unicodedata.normalize('NFC', str(text or ''))


def load_documents(datasets):
    """Use only crawled titles/descriptions; discard duplicate article URLs."""
    # set lưu URL đã gặp để loại bài trùng; json.loads đổi chuỗi JSON thành dict.
    seen = set()
    for path, collection in datasets:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        for article in payload['articles']:
            # Bỏ phần #... ở cuối URL: cùng bài nhưng khác fragment vẫn là một bài.
            url = urldefrag(str(article.get('url') or ''))[0]
            title = normalize(article.get('title')).strip()
            if not title or urlparse(url).scheme not in ('http', 'https') or url in seen:
                continue
            seen.add(url)
            # yield trả từng bài cho vòng lặp gọi hàm, thay vì tạo thêm một list lớn.
            # Chuẩn hóa hai nguồn về cùng bộ trường mà Schema sẽ khai báo.
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
    # Khóa này chỉ dùng lúc mở/tạo index, không bao quanh từng lần tìm kiếm.
    with FileLock(str(index_dir) + '.lock', timeout=120):
        # Nhánh quan trọng cho yêu cầu "index một lần": return kết thúc hàm ngay.
        # Nếu đã có index, không đi xuống phần đọc JSON và ghi tài liệu nữa.
        if index.exists_in(str(index_dir)):
            return index.open_dir(str(index_dir))
        if index_dir.exists():
            raise RuntimeError(f'Incomplete index at {index_dir}. Remove it and restart.')
        # No English stopword removal or stemming: the crawl is Vietnamese.
        # Analyzer tách văn bản thành các token rồi chuyển thành chữ thường.
        # Ví dụ: "Cao tốc" -> "cao", "tốc" (đây chưa phải tách từ ghép tiếng Việt).
        analyzer = RegexTokenizer() | LowercaseFilter()
        # Schema mô tả cấu trúc mỗi document (một bài viết).
        # TEXT: tách thành token để tìm trong văn bản. ID: giữ nguyên giá trị.
        # stored=True: lưu giá trị gốc để lấy ra hiển thị kết quả.
        # unique=True đánh dấu khóa URL; bước loại trùng thực tế nằm ở seen.
        schema = Schema(
            url=ID(stored=True, unique=True),
            title=TEXT(stored=True, analyzer=analyzer),
            description=TEXT(stored=True, analyzer=analyzer),
            publisher=ID(stored=True), collection=ID(stored=True),
            published=ID(stored=True),
        )
        # Tạo trong thư mục tạm; chỉ chuyển sang .whoosh_index sau khi ghi xong.
        # Như vậy lần chạy bị lỗi không để lại index dở dang ở đường dẫn chính.
        staging = Path(tempfile.mkdtemp(prefix='.whoosh-build-', dir=index_dir.parent))
        try:
            ix = index.create_in(str(staging), schema)
            # Writer là đối tượng chuyên thêm tài liệu vào index.
            writer = ix.writer()
            try:
                count = 0
                for document in load_documents(datasets):
                    # ** giải nén dict thành đối số: title=..., url=..., ...
                    writer.add_document(**document)
                    count += 1
                if not count:
                    raise ValueError('The datasets contain no usable articles.')
                # commit hoàn tất và lưu các thay đổi xuống ổ đĩa.
                writer.commit()
            except BaseException:
                writer.cancel()
                raise
            ix.close()
            os.replace(staging, index_dir)
        # finally luôn chạy, dù thành công hay có lỗi: dọn thư mục tạm nếu còn.
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return index.open_dir(str(index_dir))


def search_documents(ix, query, collection='All sources', match_all=True, page=1, page_size=10):
    """Return plain data so no live Whoosh hits escape the searcher context."""
    if not query.strip():
        return {'total': 0, 'pages': 0, 'page': 1, 'documents': []}
    # Parser chuyển chuỗi người dùng nhập thành đối tượng truy vấn Whoosh.
    # Tìm trong cả title và description; title có trọng số ưu tiên 2.0.
    # AND: cần tất cả từ; OR: chỉ cần một từ (nếu không ghi toán tử rõ ràng).
    parser = MultifieldParser(
        ['title', 'description'], schema=ix.schema,
        fieldboosts={'title': 2.0}, group=AndGroup if match_all else OrGroup,
    )
    parsed = parser.parse(normalize(query))
    # None nghĩa là không lọc nguồn. Term yêu cầu collection khớp chính xác.
    source_filter = None if collection == 'All sources' else Term('collection', collection)
    # A fresh searcher per request avoids sharing mutable readers across sessions.
    # `with` tự đóng searcher sau khi dùng. Index được giữ, searcher dùng ngắn hạn.
    with ix.searcher() as searcher:
        # Whoosh xếp bài theo độ liên quan; limit=None lấy tất cả bài khớp.
        # Với bộ dữ liệu nhỏ này, lấy đủ kết quả rồi cắt trang ở bên dưới.
        results = searcher.search(parsed, filter=source_filter, limit=None)
        total = len(results)
        # Chia lấy phần nguyên có làm tròn lên: 21 bài / 10 bài mỗi trang -> 3 trang.
        pages = (total + page_size - 1) // page_size
        page = max(1, min(page, pages or 1))
        # Trang 1 bắt đầu ở vị trí 0, trang 2 ở vị trí page_size.
        start = (page - 1) * page_size
        # Sao chép các trường sang dict trước khi searcher đóng.
        # hit.score là điểm xếp hạng, không phải phần trăm độ chính xác.
        documents = [dict(hit.fields(), score=hit.score) for hit in results[start:start + page_size]]
        return {'total': total, 'pages': pages, 'page': page, 'documents': documents}
