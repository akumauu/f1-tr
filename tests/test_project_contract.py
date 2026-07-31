import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ProjectContractTests(unittest.TestCase):
    def test_static_data_contract_is_documented(self):
        docs = read("docs/technical-overview.md")

        required_contract_terms = [
            "public/data/",
            "manifest.json",
            "seasons/{year}/meetings.json",
            "meetings/{meeting_key}/sessions.json",
            "sessions/{session_key}/laps.json",
            "sessions/{session_key}/lap-deltas.json",
            "sessions/{session_key}/degradation.json",
            "sessions/{session_key}/radio.json",
            "前端动态查询边界",
            "旧版 Gin API 合同只保留为本地调试参考",
        ]

        for term in required_contract_terms:
            self.assertIn(term, docs)

    def test_migration_tables_and_views_are_documented(self):
        migrations = "\n".join(
            [
                read("backend/migrations/001_create_tables.up.sql"),
                read("backend/migrations/002_create_views.up.sql"),
            ]
        )
        docs = read("docs/database.md")

        tables = re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", migrations)
        views = re.findall(r"CREATE OR REPLACE VIEW\s+([a-z_]+)", migrations)

        self.assertGreaterEqual(len(tables), 1)
        self.assertGreaterEqual(len(views), 1)

        for name in tables + views:
            self.assertIn(f"`{name}`", docs)

    def test_known_blockers_are_explicitly_tracked(self):
        handlers = read("backend/internal/api/handlers.go")
        go_mod = read("backend/go.mod")
        deepseek = read("backend/internal/translator/deepseek_client.go")
        database = read("backend/internal/database/postgres.go")
        docs = read("docs/progress.md")

        if "json.Unmarshal" in handlers and '"encoding/json"' not in handlers:
            self.assertIn("P0-01", docs)

        if "require" not in go_mod:
            self.assertIn("P0-02", docs)

        async_context_pattern = re.compile(
            r"go func\(\)\s*\{.*?c\.Request\.Context\(\)", re.DOTALL
        )
        if async_context_pattern.search(handlers):
            self.assertIn("P0-03", docs)

        retry_reuses_request = re.search(
            r"NewRequestWithContext.*?for attempt := 0; attempt < 3; attempt\+\+.*?Do\(req\)",
            deepseek,
            re.DOTALL,
        )
        if retry_reuses_request:
            self.assertIn("P1-01", docs)

        if "http.Get(audioURL)" in handlers:
            self.assertIn("P2-01", docs)

        if "*.up.sql" not in database or "filepath.Glob" not in database:
            self.assertIn("P2-03", docs)

    def test_exporter_and_frontend_mainline_exists(self):
        expected_paths = [
            "backend/cmd/exporter/main.go",
            "backend/internal/exporter/exporter.go",
            "backend/internal/exporter/store.go",
            "backend/internal/exporter/exporter_test.go",
            "backend/migrations/003_add_idempotency_indexes.up.sql",
            "frontend/package.json",
            "requirements.txt",
            "tools/export_catalunya_static.py",
            "frontend/src/index.html",
            "frontend/src/app.js",
            "frontend/src/styles.css",
            "frontend/scripts/build.js",
            "frontend/scripts/check-build.js",
            "frontend/public/data/manifest.json",
        ]
        for relative_path in expected_paths:
            self.assertTrue((ROOT / relative_path).exists(), relative_path)

        exporter_main = read("backend/cmd/exporter/main.go")
        frontend_app = read("frontend/src/app.js")
        package_json = read("frontend/package.json")
        catalunya_exporter = read("tools/export_catalunya_static.py")
        requirements = read("requirements.txt")

        self.assertIn('flag.String("out"', exporter_main)
        self.assertIn("manifest.json", frontend_app)
        self.assertIn("lap_deltas", frontend_app)
        self.assertIn('"build"', package_json)
        self.assertIn('"test"', package_json)
        self.assertIn("DEFAULT_MEETING_KEY = 1287", catalunya_exporter)
        self.assertIn("DEFAULT_SESSION_KEY = 11307", catalunya_exporter)
        self.assertIn("fastf1", requirements)

    def test_catalunya_sample_data_is_current_default(self):
        manifest = json.loads(read("frontend/public/data/manifest.json"))

        self.assertEqual(manifest["default_session_key"], 11307)
        self.assertEqual(manifest["meetings"][0]["meeting_key"], 1287)
        self.assertEqual(manifest["meetings"][0]["meeting_name"], "Barcelona Grand Prix")
        self.assertGreaterEqual(manifest["sessions"][0]["record_counts"]["laps"], 300)
        self.assertGreaterEqual(manifest["sessions"][0]["record_counts"]["radio"], 40)

    def test_scope_boundaries_are_explicit(self):
        docs = read("docs/technical-overview.md")

        required_phrases = [
            "v1 主线",
            "v1 不做",
            "不提供常驻 Gin API 服务器给公网用户访问",
            "不实现 Cloudflare Workers",
            "前端 SPA",
            "不实现 STT / ASR 音频转文字",
        ]
        for phrase in required_phrases:
            self.assertIn(phrase, docs)

    def test_document_map_and_readme_point_to_canonical_docs(self):
        readme = read("README.md")
        document_map = read("docs/README.md")
        strategy = read("docs/test-strategy.md")

        self.assertIn("docs/README.md", readme)
        self.assertIn("docs/technical-overview.md", readme)
        self.assertIn("docs/database.md", readme)
        self.assertIn("docs/algorithm-models.md", readme)
        self.assertIn("docs/implementation-results.md", readme)
        self.assertIn("docs/progress.md", readme)
        self.assertIn("docs/test-strategy.md", readme)
        for name in [
            "technical-overview.md",
            "database.md",
            "algorithm-models.md",
            "implementation-results.md",
            "progress.md",
        ]:
            self.assertIn(name, document_map)
        self.assertIn("python3 -m unittest discover -s tests", strategy)
        self.assertIn("go test ./...", strategy)

    def test_agents_file_routes_to_document_map(self):
        agents = read("AGENTS.md")

        self.assertIn("docs/README.md", agents)
        self.assertIn("先阅读", agents)


if __name__ == "__main__":
    unittest.main()
