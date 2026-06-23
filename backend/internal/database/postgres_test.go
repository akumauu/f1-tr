package database

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMigrationFilesReturnsSortedUpMigrations(t *testing.T) {
	dir := t.TempDir()
	files := map[string]string{
		"002_second.up.sql":  "select 2;",
		"001_first.up.sql":   "select 1;",
		"001_first.down.sql": "select -1;",
		"README.md":          "ignore",
	}

	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}

	got, err := migrationFiles(dir)
	if err != nil {
		t.Fatalf("migrationFiles() error = %v", err)
	}

	want := []string{
		filepath.Join(dir, "001_first.up.sql"),
		filepath.Join(dir, "002_second.up.sql"),
	}
	if len(got) != len(want) {
		t.Fatalf("migrationFiles() len = %d, want %d: %v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("migrationFiles()[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}
