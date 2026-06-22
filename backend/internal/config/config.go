package config

import (
	"fmt"
	"os"
	"strconv"
)

// Config holds all configuration for the application.
type Config struct {
	Database   DatabaseConfig
	DeepSeek   DeepSeekConfig
	API        APIConfig
	OpenF1     OpenF1Config
}

type DatabaseConfig struct {
	URL string
}

type DeepSeekConfig struct {
	APIKey  string
	Model   string
	BaseURL string
}

type APIConfig struct {
	Port string
	Mode string
}

type OpenF1Config struct {
	BaseURL string
}

// Load reads configuration from environment variables.
func Load() *Config {
	return &Config{
		Database: DatabaseConfig{
			URL: getEnv("DATABASE_URL", "postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable"),
		},
		DeepSeek: DeepSeekConfig{
			APIKey:  getEnv("DEEPSEEK_API_KEY", ""),
			Model:   getEnv("DEEPSEEK_MODEL", "deepseek-chat"),
			BaseURL: getEnv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
		},
		API: APIConfig{
			Port: getEnv("API_PORT", "8080"),
			Mode: getEnv("GIN_MODE", "debug"),
		},
		OpenF1: OpenF1Config{
			BaseURL: getEnv("OPENF1_BASE_URL", "https://api.openf1.org/v1"),
		},
	}
}

// Addr returns the API listen address.
func (c *APIConfig) Addr() string {
	return fmt.Sprintf(":%s", c.Port)
}

// GetBatchSize returns the batch size for translation worker.
func GetBatchSize() int {
	v, _ := strconv.Atoi(getEnv("TRANSLATE_BATCH_SIZE", "10"))
	if v <= 0 {
		return 10
	}
	return v
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
