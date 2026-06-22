package main

import (
	"context"
	"flag"
	"log"

	"f1tr/internal/config"
	"f1tr/internal/database"
	"f1tr/internal/translator"
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	batchSize := flag.Int("batch", 10, "Number of radios to translate per batch")
	flag.Parse()

	cfg := config.Load()

	if cfg.DeepSeek.APIKey == "" || cfg.DeepSeek.APIKey == "your_deepseek_api_key_here" {
		log.Fatal("[Translator] DEEPSEEK_API_KEY is not set. Please set it in .env or environment.")
	}

	db, err := database.New(cfg.Database)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Close()

	client := translator.NewDeepSeekClient(cfg.DeepSeek.APIKey, cfg.DeepSeek.BaseURL, cfg.DeepSeek.Model)
	worker := translator.NewWorker(db.Pool, client, *batchSize)

	log.Printf("[Translator] Starting translation worker (batch size: %d)", *batchSize)
	if err := worker.Run(context.Background()); err != nil {
		log.Fatalf("Translation failed: %v", err)
	}
	log.Println("[Translator] Complete ✓")
}
