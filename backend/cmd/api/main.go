package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"f1tr/internal/api"
	"f1tr/internal/config"
	"f1tr/internal/database"
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Println("[API] Starting F1 TR Analysis API...")

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
		log.Printf("[API] WARN: Migration error (may already exist): %v", err)
	}

	server := api.NewServer(db.Pool, cfg)
	router := server.SetupRouter()

	// Graceful shutdown
	go func() {
		log.Printf("[API] Listening on %s", cfg.API.Addr())
		if err := router.Run(cfg.API.Addr()); err != nil {
			log.Fatalf("Server error: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("[API] Shutting down...")
}
