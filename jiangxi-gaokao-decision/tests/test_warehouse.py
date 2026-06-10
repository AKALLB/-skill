import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_DIR / "scripts" / "warehouse.py"


def load_warehouse_module():
    spec = importlib.util.spec_from_file_location("warehouse", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.module = load_warehouse_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "warehouse.sqlite"
        self.module.initialize(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def test_initialize_is_idempotent_and_records_schema_version(self):
        self.module.initialize(self.db_path)
        with self.connect() as connection:
            version = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()["version"]
        self.assertGreaterEqual(version, 1)

    def test_identical_snapshot_content_is_deduplicated(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "jxeea-test",
                "name": "江西省教育考试院测试源",
                "url": "https://example.invalid/jxeea",
                "authority": "official",
                "module": "admissions",
                "freshness_days": 30,
            },
        )
        content = b"official snapshot"
        first = self.module.store_snapshot(
            self.db_path, source_id, content, "text/html", "2025"
        )
        second = self.module.store_snapshot(
            self.db_path, source_id, content, "text/html", "2025"
        )
        self.assertEqual(first, second)
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM snapshots"
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_changed_snapshot_preserves_history(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "policy-test",
                "name": "政策测试源",
                "url": "https://example.invalid/policy",
                "authority": "official",
                "module": "industry",
                "freshness_days": 90,
            },
        )
        first = self.module.store_snapshot(
            self.db_path, source_id, b"version one", "text/plain", "2025"
        )
        second = self.module.store_snapshot(
            self.db_path, source_id, b"version two", "text/plain", "2026"
        )
        self.assertNotEqual(first, second)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT content_hash FROM snapshots ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [row["content_hash"] for row in rows],
            [
                hashlib.sha256(b"version one").hexdigest(),
                hashlib.sha256(b"version two").hexdigest(),
            ],
        )

    def test_evidence_trace_reaches_official_source(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "job-test",
                "name": "江西人事考试测试源",
                "url": "https://example.invalid/jobs",
                "authority": "official",
                "module": "jobs",
                "freshness_days": 30,
            },
        )
        snapshot_id = self.module.store_snapshot(
            self.db_path, source_id, b"position table", "text/csv", "2026"
        )
        evidence_id = self.module.add_evidence(
            self.db_path,
            snapshot_id=snapshot_id,
            claim_type="job_requirement",
            claim_key="position-001",
            statement="该岗位要求计算机类本科",
            applicable_year=2026,
            confidence="high",
        )
        trace = self.module.trace_evidence(self.db_path, evidence_id)
        self.assertEqual(trace["source_authority"], "official")
        self.assertEqual(trace["source_key"], "job-test")
        self.assertEqual(trace["statement"], "该岗位要求计算机类本科")

    def test_status_marks_overdue_sources_as_stale(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "stale-test",
                "name": "过期测试源",
                "url": "https://example.invalid/stale",
                "authority": "official",
                "module": "ai",
                "freshness_days": 7,
            },
        )
        old_time = datetime.now(timezone.utc) - timedelta(days=20)
        with self.connect() as connection:
            connection.execute(
                "UPDATE sources SET last_checked_at = ? WHERE id = ?",
                (old_time.isoformat(), source_id),
            )
            connection.commit()
        status = self.module.get_status(self.db_path)
        item = next(row for row in status if row["key"] == "stale-test")
        self.assertEqual(item["freshness"], "stale")

    def test_status_does_not_treat_failed_fetch_as_fresh(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "failed-test",
                "name": "失败测试源",
                "url": "https://example.invalid/failed",
                "authority": "official",
                "module": "jobs",
                "freshness_days": 30,
            },
        )
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "UPDATE sources SET last_checked_at=? WHERE id=?", (now, source_id)
            )
            connection.execute(
                """
                INSERT INTO fetch_runs(
                  source_id,started_at,finished_at,status,error
                ) VALUES(?,?,?,?,?)
                """,
                (source_id, now, now, "error", "blocked"),
            )
            connection.commit()
        status = self.module.get_status(self.db_path)
        item = next(row for row in status if row["key"] == "failed-test")
        self.assertEqual(item["freshness"], "error")

    def test_validate_rejects_third_party_only_critical_evidence(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "third-party-test",
                "name": "第三方测试源",
                "url": "https://example.invalid/blog",
                "authority": "third_party",
                "module": "admissions",
                "freshness_days": 7,
            },
        )
        snapshot_id = self.module.store_snapshot(
            self.db_path, source_id, b"unverified", "text/html", "2026"
        )
        self.module.add_evidence(
            self.db_path,
            snapshot_id=snapshot_id,
            claim_type="admission_cutoff",
            claim_key="critical-claim",
            statement="某专业组最低位次12345",
            applicable_year=2026,
            confidence="high",
            critical=True,
        )
        issues = self.module.validate_warehouse(self.db_path)
        self.assertTrue(
            any(issue["code"] == "critical_third_party_only" for issue in issues)
        )

    def test_text_chunks_are_searchable_and_traceable(self):
        source_id = self.module.upsert_source(
            self.db_path,
            {
                "key": "charter-test",
                "name": "高校招生章程测试源",
                "url": "https://example.invalid/charter",
                "authority": "official",
                "module": "admissions",
                "freshness_days": 30,
            },
        )
        snapshot_id = self.module.store_snapshot(
            self.db_path,
            source_id,
            "专业组内调剂，不录取色觉异常考生。".encode("utf-8"),
            "text/plain",
            "2026",
        )
        self.module.index_text(
            self.db_path,
            snapshot_id,
            "专业组内调剂，不录取色觉异常考生。",
        )
        results = self.module.search_text(self.db_path, "色觉异常")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_key"], "charter-test")
        self.assertEqual(results[0]["snapshot_id"], snapshot_id)

    def test_html_indexing_keeps_visible_text_and_drops_scripts(self):
        text = self.module.searchable_text(
            b"<html><style>.x{color:red}</style><body>Official policy"
            b"<script>secretCode()</script></body></html>",
            "text/html",
        )
        self.assertIn("Official policy", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("secretCode", text)


class SkillContractTests(unittest.TestCase):
    def test_skill_contract_forbids_popularity_scoring(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("热门度不得加分", text)
        self.assertIn("不得生成万能总分", text)
        self.assertIn("第三方来源只能作为线索", text)
        self.assertIn("六层架构", text)
        self.assertIn("结论 → 数据 → 原始来源", text)
        self.assertIn("主路径", text)
        self.assertIn("备选路径", text)
        self.assertIn("保底路径", text)

    def test_source_registry_has_four_required_modules(self):
        registry = json.loads(
            (SKILL_DIR / "references" / "sources.json").read_text(encoding="utf-8")
        )
        modules = {source["module"] for source in registry["sources"]}
        self.assertTrue({"admissions", "jobs", "industry", "ai"}.issubset(modules))


if __name__ == "__main__":
    unittest.main()
