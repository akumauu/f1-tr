import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ProjectContractTests(unittest.TestCase):
    def test_router_endpoints_are_documented(self):
        router = read("backend/internal/api/router.go")
        docs = read("docs/technical-overview.md")

        endpoints = {"/health"}
        for path in re.findall(r"v1\.(?:GET|POST)\(\"([^\"]+)\"", router):
            endpoints.add("/api/v1" + path)

        for endpoint in sorted(endpoints):
            self.assertIn(endpoint, docs)

    def test_migration_tables_and_views_are_documented(self):
        migrations = "\n".join(
            [
                read("backend/migrations/001_create_tables.up.sql"),
                read("backend/migrations/002_create_views.up.sql"),
            ]
        )
        docs = read("docs/technical-overview.md")

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
        docs = read("docs/technical-overview.md")

        if "json.Unmarshal" in handlers and '"encoding/json"' not in handlers:
            self.assertIn("P0-01", docs)

        if "require" not in go_mod:
            self.assertIn("P0-02", docs)

        async_context_pattern = re.compile(
            r"go func\(\)\s*\{.*?c\.Request\.Context\(\)", re.DOTALL
        )
        if async_context_pattern.search(handlers):
            self.assertIn("P0-03", docs)

        retry_reuses_request = (
            "NewRequestWithContext" in deepseek
            and "for attempt := 0; attempt < 3; attempt++" in deepseek
            and "Do(req)" in deepseek
        )
        if retry_reuses_request:
            self.assertIn("P1-01", docs)

        if "http.Get(audioURL)" in handlers:
            self.assertIn("P2-01", docs)

    def test_scope_boundaries_are_explicit(self):
        docs = read("docs/technical-overview.md")

        required_phrases = [
            "v1 主线",
            "v1 不做",
            "不引入 Rust 主 API",
            "不实现 Cloudflare Workers",
            "不实现前端 Dashboard",
            "不实现 STT / ASR 音频转文字",
        ]
        for phrase in required_phrases:
            self.assertIn(phrase, docs)

    def test_test_strategy_and_readme_point_to_canonical_docs(self):
        readme = read("README.md")
        strategy = read("docs/test-strategy.md")

        self.assertIn("docs/technical-overview.md", readme)
        self.assertIn("docs/test-strategy.md", readme)
        self.assertIn("python3 -m unittest discover -s tests", strategy)
        self.assertIn("go test ./...", strategy)


if __name__ == "__main__":
    unittest.main()
