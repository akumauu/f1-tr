package main

import (
	"context"
	"flag"
	"log"
	"os"
	"time"

	"f1tr/internal/config"
	"f1tr/internal/database"
	"f1tr/internal/exporter"
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	outDir := flag.String("out", "../public/data", "Output directory for static JSON data")
	version := flag.String("version", "v1", "Data version written to manifest.json")
	runMigrations := flag.Bool("migrate", false, "Run database migrations before exporting")
	flag.Parse()

	cfg := config.Load()
	db, err := database.New(cfg.Database)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	if *runMigrations {
		migrationsDir := "migrations"
		if _, err := os.Stat(migrationsDir); os.IsNotExist(err) {
			migrationsDir = "backend/migrations"
		}
		if err := db.RunMigrations(ctx, migrationsDir); err != nil {
			log.Fatalf("Failed to run migrations: %v", err)
		}
	}

	staticExporter := exporter.New(exporter.NewPostgresStore(db.Pool), *version)
	manifest, err := staticExporter.ExportAll(ctx, *outDir)
	if err != nil {
		log.Fatalf("Export failed: %v", err)
	}

	log.Printf("[Exporter] Wrote %d meetings and %d sessions to %s", len(manifest.Meetings), len(manifest.Sessions), *outDir)
}
