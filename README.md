# Week 2: Whoosh news search

A Streamlit search interface using Whoosh and the Vietnamese news dataset
collected in Week 1. Both JSON datasets are included, so the project runs
independently without the crawler or external services.

## Project structure

```text
app.py                   Streamlit interface
search_engine.py         Persistent indexing and search
test_search_engine.py    Search and index reuse tests
requirements.txt         Python dependencies
baomoi_24h.json           Báo Mới dataset
vnexpress_24h.json        VnExpress dataset
```

## Run

Python 3.10 or newer. Open a terminal in the cloned repository, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

Open the local URL printed by Streamlit (normally http://localhost:8501).
Internet access is needed to install dependencies and open original article links;
indexing and searching use local files.

## Assignment requirements

| Requirement | Implementation |
| --- | --- |
| Whoosh text search (5 points) | `search_engine.py` indexes titles and descriptions, parses queries with `MultifieldParser`, and ranks matches using Whoosh's default BM25F scoring. Title matches receive a boost. |
| GUI with query and matches (4 points) | `app.py` provides a query form, source filter, all/any word selection, result counts, pagination, titles, descriptions, publication timestamps, and links. |
| Index only once (1 point) | `ensure_index()` checks for a persisted index before reading JSON or opening a writer. `@st.cache_resource` also reuses the opened index across Streamlit reruns. |

## Dataset and search behavior

The supplied snapshot contains 2,117 Báo Mới records and 65 VnExpress records.
Duplicate URLs are removed, fragments are stripped, and rows without a title or
HTTP(S) URL are skipped. The sidebar shows the actual indexed document count.
Báo Mới supplies descriptions; VnExpress supplies titles but no article bodies
or descriptions. Search uses only these available text fields, not fabricated
full text or comment metrics.

Text is Unicode-normalized and lowercased without English stemming or stopword
removal. Vietnamese accents matter. Examples:

- `cao tốc`: all words must match by default.
- `"Long Thành"`: match a phrase.
- `cao OR phẩm`: match either word.
- `title:"thực phẩm"`: search a phrase in the title.

## Why interactions do not re-index

1. On the first launch, `get_index()` calls `ensure_index()`.
2. A file lock serializes first-time initialization across processes. Documents
   are written to a temporary directory, committed, then moved to `.whoosh_index/`.
   A failed build does not publish a partial index.
3. Streamlit interactions reuse the cached index resource. Each search opens its
   own short-lived searcher, so sessions do not share a mutable searcher.
4. After an application restart or cache clear, `index.exists_in()` detects the
   committed disk index and `index.open_dir()` reopens it without reading JSON
   or indexing documents again.

Changes to the JSON files do **not** trigger automatic re-indexing. To deliberately
use a new dataset, stop all app instances, delete only `.whoosh_index/`, then
restart. Keep the directory to reuse the original indexed snapshot.

## Verify

```bash
python -m unittest -v test_search_engine.py
```

Tests cover Vietnamese/phrase/description queries, filtering, Boolean matching,
pagination, URL deduplication, recovery after a failed first build, and reopening
an index without rereading JSON or changing any index file bytes.

For a manual GUI check, launch the app, search `cao tốc`, change filters and page,
then restart the app. The indexed count should stay constant and index files
should not be rewritten.

References: [Whoosh quick start](https://whoosh.readthedocs.io/en/latest/quickstart.html)
and [Streamlit caching](https://docs.streamlit.io/develop/concepts/architecture/caching).


