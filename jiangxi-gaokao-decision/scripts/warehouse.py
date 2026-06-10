"""SQLite evidence warehouse and text retrieval CLI."""

import argparse
import contextlib
import hashlib
import html
import json
import mimetypes
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


SCHEMA_VERSION = 1
SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path.cwd() / ".skill-data" / "jiangxi-gaokao-decision"
DEFAULT_DB = DEFAULT_DATA_DIR / "warehouse.sqlite"
DEFAULT_REGISTRY = SKILL_DIR / "references" / "sources.json"


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth:
            value = data.strip()
            if value:
                self.parts.append(value)


def searchable_text(content, mime_type):
    decoded = content.decode("utf-8", errors="replace")
    if mime_type in {"text/html", "application/xhtml+xml"}:
        parser = VisibleTextParser()
        parser.feed(decoded)
        return html.unescape(" ".join(parser.parts))
    return decoded


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextlib.contextmanager
def connect(db_path):
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def initialize(db_path=DEFAULT_DB):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                authority TEXT NOT NULL CHECK(authority IN ('official','primary_research','third_party')),
                module TEXT NOT NULL CHECK(module IN ('admissions','jobs','industry','ai')),
                freshness_days INTEGER NOT NULL DEFAULT 30,
                last_checked_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                content_hash TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                applicable_period TEXT,
                fetched_at TEXT NOT NULL,
                raw_path TEXT,
                UNIQUE(source_id, content_hash)
            );
            CREATE TABLE IF NOT EXISTS fetch_runs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                http_status INTEGER,
                snapshot_id INTEGER REFERENCES snapshots(id),
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS text_chunks (
                id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                UNIQUE(snapshot_id, chunk_index)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS text_chunks_fts USING fts5(
                content,
                content='text_chunks',
                content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS text_chunks_ai AFTER INSERT ON text_chunks BEGIN
                INSERT INTO text_chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS text_chunks_ad AFTER DELETE ON text_chunks BEGIN
                INSERT INTO text_chunks_fts(text_chunks_fts, rowid, content)
                VALUES('delete', old.id, old.content);
            END;
            CREATE TABLE IF NOT EXISTS institutions (
                id INTEGER PRIMARY KEY,
                code TEXT,
                name TEXT NOT NULL,
                province TEXT,
                school_type TEXT,
                UNIQUE(code, name)
            );
            CREATE TABLE IF NOT EXISTS majors (
                id INTEGER PRIMARY KEY,
                code TEXT,
                name TEXT NOT NULL,
                category TEXT,
                UNIQUE(code, name)
            );
            CREATE TABLE IF NOT EXISTS program_groups (
                id INTEGER PRIMARY KEY,
                institution_id INTEGER NOT NULL REFERENCES institutions(id),
                year INTEGER NOT NULL,
                group_code TEXT NOT NULL,
                subject_requirements TEXT,
                tuition INTEGER,
                sino_foreign INTEGER NOT NULL DEFAULT 0,
                campus TEXT,
                UNIQUE(institution_id, year, group_code)
            );
            CREATE TABLE IF NOT EXISTS group_majors (
                group_id INTEGER NOT NULL REFERENCES program_groups(id),
                major_id INTEGER NOT NULL REFERENCES majors(id),
                PRIMARY KEY(group_id, major_id)
            );
            CREATE TABLE IF NOT EXISTS admission_results (
                id INTEGER PRIMARY KEY,
                group_id INTEGER NOT NULL REFERENCES program_groups(id),
                batch TEXT,
                min_score REAL,
                min_rank INTEGER,
                plan_count INTEGER,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                UNIQUE(group_id, batch, snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS job_postings (
                id INTEGER PRIMARY KEY,
                external_key TEXT,
                employer TEXT NOT NULL,
                route TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                education TEXT,
                fresh_graduate_only INTEGER,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                UNIQUE(external_key, snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS job_major_requirements (
                job_id INTEGER NOT NULL REFERENCES job_postings(id),
                major_category TEXT NOT NULL,
                requirement_text TEXT,
                PRIMARY KEY(job_id, major_category)
            );
            CREATE TABLE IF NOT EXISTS industry_projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                sector TEXT,
                stage TEXT,
                investment_amount REAL,
                undergraduate_jobs_evidence TEXT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
            );
            CREATE TABLE IF NOT EXISTS policy_signals (
                id INTEGER PRIMARY KEY,
                topic TEXT NOT NULL,
                signal_level TEXT NOT NULL,
                statement TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
            );
            CREATE TABLE IF NOT EXISTS ai_task_signals (
                id INTEGER PRIMARY KEY,
                occupation TEXT NOT NULL,
                task TEXT NOT NULL,
                effect TEXT NOT NULL,
                horizon TEXT,
                statement TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                claim_type TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                statement TEXT NOT NULL,
                applicable_year INTEGER,
                confidence TEXT NOT NULL,
                critical INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_type, claim_key);
            CREATE INDEX IF NOT EXISTS idx_snapshots_source ON snapshots(source_id, fetched_at);
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, utc_now()),
        )
        connection.commit()
    return db_path


def upsert_source(db_path, source):
    initialize(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO sources(key,name,url,authority,module,freshness_days,notes)
            VALUES(:key,:name,:url,:authority,:module,:freshness_days,:notes)
            ON CONFLICT(key) DO UPDATE SET
              name=excluded.name, url=excluded.url, authority=excluded.authority,
              module=excluded.module, freshness_days=excluded.freshness_days,
              notes=excluded.notes, active=1
            """,
            {
                **source,
                "freshness_days": source.get("freshness_days", 30),
                "notes": source.get("notes"),
            },
        )
        row = connection.execute(
            "SELECT id FROM sources WHERE key = ?", (source["key"],)
        ).fetchone()
        connection.commit()
        return row["id"]


def register_sources(db_path=DEFAULT_DB, registry_path=DEFAULT_REGISTRY):
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    return [upsert_source(db_path, item) for item in registry["sources"]]


def _raw_extension(mime_type):
    return mimetypes.guess_extension(mime_type.split(";")[0]) or ".bin"


def store_snapshot(
    db_path,
    source_id,
    content,
    mime_type,
    applicable_period=None,
    raw_root=None,
):
    initialize(db_path)
    digest = hashlib.sha256(content).hexdigest()
    with connect(db_path) as connection:
        existing = connection.execute(
            "SELECT id FROM snapshots WHERE source_id=? AND content_hash=?",
            (source_id, digest),
        ).fetchone()
        if existing:
            return existing["id"]
        fetched_at = utc_now()
        cursor = connection.execute(
            """
            INSERT INTO snapshots(
              source_id,content_hash,mime_type,applicable_period,fetched_at,raw_path
            ) VALUES(?,?,?,?,?,NULL)
            """,
            (source_id, digest, mime_type, applicable_period, fetched_at),
        )
        snapshot_id = cursor.lastrowid
        if raw_root:
            raw_root = Path(raw_root)
            raw_root.mkdir(parents=True, exist_ok=True)
            raw_path = raw_root / f"{source_id}-{snapshot_id}-{digest[:12]}{_raw_extension(mime_type)}"
            raw_path.write_bytes(content)
            connection.execute(
                "UPDATE snapshots SET raw_path=? WHERE id=?",
                (str(raw_path.resolve()), snapshot_id),
            )
        connection.commit()
        return snapshot_id


def _chunks(text, size=1200, overlap=150):
    normalized = " ".join(text.split())
    if not normalized:
        return []
    step = max(1, size - overlap)
    return [normalized[start : start + size] for start in range(0, len(normalized), step)]


def index_text(db_path, snapshot_id, text, chunk_size=1200):
    with connect(db_path) as connection:
        connection.execute("DELETE FROM text_chunks WHERE snapshot_id=?", (snapshot_id,))
        for index, chunk in enumerate(_chunks(text, chunk_size)):
            connection.execute(
                "INSERT INTO text_chunks(snapshot_id,chunk_index,content) VALUES(?,?,?)",
                (snapshot_id, index, chunk),
            )
        connection.commit()


def search_text(db_path, query, limit=10):
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT tc.snapshot_id, tc.chunk_index, tc.content,
                   s.key AS source_key, s.name AS source_name, s.authority,
                   sn.fetched_at, sn.applicable_period
            FROM text_chunks tc
            JOIN snapshots sn ON sn.id=tc.snapshot_id
            JOIN sources s ON s.id=sn.source_id
            WHERE tc.content LIKE ?
            ORDER BY sn.fetched_at DESC
            LIMIT ?
            """,
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]


def add_evidence(
    db_path,
    snapshot_id,
    claim_type,
    claim_key,
    statement,
    applicable_year=None,
    confidence="medium",
    critical=False,
):
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO evidence(
              snapshot_id,claim_type,claim_key,statement,applicable_year,
              confidence,critical,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_id,
                claim_type,
                claim_key,
                statement,
                applicable_year,
                confidence,
                int(critical),
                utc_now(),
            ),
        )
        connection.commit()
        return cursor.lastrowid


def trace_evidence(db_path, evidence_id):
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT e.*, sn.content_hash, sn.raw_path, sn.fetched_at,
                   s.key AS source_key, s.name AS source_name,
                   s.url AS source_url, s.authority AS source_authority
            FROM evidence e
            JOIN snapshots sn ON sn.id=e.snapshot_id
            JOIN sources s ON s.id=sn.source_id
            WHERE e.id=?
            """,
            (evidence_id,),
        ).fetchone()
        return dict(row) if row else None


def get_status(db_path=DEFAULT_DB):
    initialize(db_path)
    now = datetime.now(timezone.utc)
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT s.*,
              (SELECT MAX(fetched_at) FROM snapshots sn WHERE sn.source_id=s.id)
              AS latest_snapshot_at,
              (SELECT status FROM fetch_runs fr WHERE fr.source_id=s.id
               ORDER BY fr.id DESC LIMIT 1) AS latest_fetch_status,
              (SELECT error FROM fetch_runs fr WHERE fr.source_id=s.id
               ORDER BY fr.id DESC LIMIT 1) AS latest_fetch_error
            FROM sources s WHERE active=1 ORDER BY module,key
            """
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        checked = item["last_checked_at"]
        if item["latest_fetch_status"] == "error":
            freshness = "error"
        elif not checked:
            freshness = "missing"
        else:
            checked_at = datetime.fromisoformat(checked)
            deadline = checked_at + timedelta(days=item["freshness_days"])
            freshness = "fresh" if deadline >= now else "stale"
        item["freshness"] = freshness
        result.append(item)
    return result


def validate_warehouse(db_path=DEFAULT_DB):
    issues = []
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT e.claim_type,e.claim_key,
                   SUM(CASE WHEN s.authority='official' THEN 1 ELSE 0 END) official_count
            FROM evidence e
            JOIN snapshots sn ON sn.id=e.snapshot_id
            JOIN sources s ON s.id=sn.source_id
            WHERE e.critical=1
            GROUP BY e.claim_type,e.claim_key
            """
        ).fetchall()
        for row in rows:
            if row["official_count"] == 0:
                issues.append(
                    {
                        "code": "critical_third_party_only",
                        "claim_type": row["claim_type"],
                        "claim_key": row["claim_key"],
                    }
                )
    return issues


def refresh(db_path=DEFAULT_DB, registry_path=DEFAULT_REGISTRY, force=False):
    initialize(db_path)
    register_sources(db_path, registry_path)
    data_dir = Path(db_path).parent
    results = []
    for source in get_status(db_path):
        if not force and source["freshness"] == "fresh":
            results.append({"key": source["key"], "status": "skipped_fresh"})
            continue
        started = utc_now()
        with connect(db_path) as connection:
            cursor = connection.execute(
                "INSERT INTO fetch_runs(source_id,started_at,status) VALUES(?,?,'running')",
                (source["id"], started),
            )
            run_id = cursor.lastrowid
            connection.commit()
        try:
            request = urllib.request.Request(
                source["url"],
                headers={"User-Agent": "Mozilla/5.0 EvidenceWarehouse/1.0"},
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                content = response.read()
                mime_type = response.headers.get_content_type()
                http_status = response.status
            snapshot_id = store_snapshot(
                db_path,
                source["id"],
                content,
                mime_type,
                raw_root=data_dir / "raw",
            )
            if mime_type.startswith("text/") or mime_type in {
                "application/json",
                "application/xml",
            }:
                index_text(db_path, snapshot_id, searchable_text(content, mime_type))
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE sources SET last_checked_at=? WHERE id=?",
                    (utc_now(), source["id"]),
                )
                connection.execute(
                    """
                    UPDATE fetch_runs SET finished_at=?,status='ok',
                      http_status=?,snapshot_id=? WHERE id=?
                    """,
                    (utc_now(), http_status, snapshot_id, run_id),
                )
                connection.commit()
            results.append({"key": source["key"], "status": "ok", "snapshot_id": snapshot_id})
        except Exception as error:
            with connect(db_path) as connection:
                connection.execute(
                    "UPDATE sources SET last_checked_at=? WHERE id=?",
                    (utc_now(), source["id"]),
                )
                connection.execute(
                    "UPDATE fetch_runs SET finished_at=?,status='error',error=? WHERE id=?",
                    (utc_now(), str(error), run_id),
                )
                connection.commit()
            results.append({"key": source["key"], "status": "error", "error": str(error)})
    return results


def import_document(db_path, source_key, input_path, applicable_period=None):
    input_path = Path(input_path)
    content = input_path.read_bytes()
    with connect(db_path) as connection:
        source = connection.execute(
            "SELECT id FROM sources WHERE key=?", (source_key,)
        ).fetchone()
    if not source:
        raise ValueError(f"Unknown source key: {source_key}")
    mime_type = mimetypes.guess_type(input_path.name)[0] or "application/octet-stream"
    snapshot_id = store_snapshot(
        db_path,
        source["id"],
        content,
        mime_type,
        applicable_period,
        Path(db_path).parent / "raw",
    )
    if mime_type.startswith("text/") or input_path.suffix.lower() in {".md", ".json", ".csv"}:
        index_text(db_path, snapshot_id, searchable_text(content, mime_type))
    return snapshot_id


def run_select(db_path, sql):
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ValueError("query only accepts SELECT or WITH statements")
    with connect(db_path) as connection:
        return [dict(row) for row in connection.execute(sql).fetchall()]


def build_parser():
    parser = argparse.ArgumentParser(description="Jiangxi Gaokao evidence warehouse")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--force", action="store_true")
    refresh_parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    sub.add_parser("status")
    query_parser = sub.add_parser("query")
    query_parser.add_argument("sql")
    search_parser = sub.add_parser("search")
    search_parser.add_argument("text")
    search_parser.add_argument("--limit", type=int, default=10)
    import_parser = sub.add_parser("import")
    import_parser.add_argument("source_key")
    import_parser.add_argument("path")
    import_parser.add_argument("--period")
    trace_parser = sub.add_parser("trace")
    trace_parser.add_argument("evidence_id", type=int)
    sub.add_parser("validate")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    if args.command == "init":
        initialize(db_path)
        register_sources(db_path)
        output = {"database": str(db_path.resolve()), "status": "initialized"}
    elif args.command == "refresh":
        output = refresh(db_path, args.registry, args.force)
    elif args.command == "status":
        output = get_status(db_path)
    elif args.command == "query":
        output = run_select(db_path, args.sql)
    elif args.command == "search":
        output = search_text(db_path, args.text, args.limit)
    elif args.command == "import":
        output = {
            "snapshot_id": import_document(
                db_path, args.source_key, args.path, args.period
            )
        }
    elif args.command == "trace":
        output = trace_evidence(db_path, args.evidence_id)
    elif args.command == "validate":
        output = validate_warehouse(db_path)
    else:
        raise AssertionError(args.command)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
