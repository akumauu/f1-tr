package main

import (
	"context"
	"flag"
	"log"
	"os"

	"f1tr/internal/config"
	"f1tr/internal/database"
	"f1tr/internal/ingester"
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	sessionKey := flag.Int("session-key", 0, "Session key to ingest (fetches all data for this session)")
	year := flag.Int("year", 2026, "Year to fetch meetings for")
	meetingsOnly := flag.Bool("meetings-only", false, "Only fetch meetings and sessions (no lap/radio data)")
	flag.Parse()

	cfg := config.Load()

	db, err := database.New(cfg.Database)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Close()

	// Run migrations
	migrationsDir := "migrations"
	if _, err := os.Stat(migrationsDir); os.IsNotExist(err) {
		migrationsDir = "backend/migrations"
	}
	if err := db.RunMigrations(context.Background(), migrationsDir); err != nil {
		log.Printf("[Ingester] WARN: Migration error: %v", err)
	}

	client := ingester.NewOpenF1Client(cfg.OpenF1.BaseURL)
	pipeline := ingester.NewPipeline(db.Pool)
	fetcher := ingester.NewFetcher(client, pipeline)

	ctx := context.Background()

	if *meetingsOnly || *sessionKey == 0 {
		log.Printf("[Ingester] Fetching meetings for year %d", *year)
		if err := fetcher.FetchMeetingsForYear(ctx, *year); err != nil {
			log.Fatalf("Failed to fetch meetings: %v", err)
		}
		if *meetingsOnly {
			log.Println("[Ingester] Meetings-only mode complete ✓")
			return
		}
	}

	if *sessionKey > 0 {
		log.Printf("[Ingester] Fetching session %d", *sessionKey)
		if err := fetcher.FetchSession(ctx, *sessionKey); err != nil {
			log.Fatalf("Failed to fetch session: %v", err)
		}
		log.Printf("[Ingester] Session %d ingestion complete ✓", *sessionKey)
	} else {
		log.Println("[Ingester] No session key specified. Use -session-key=<key> to ingest session data.")
		log.Println("[Ingester] Run with -meetings-only to just fetch meetings list.")
	}
}
