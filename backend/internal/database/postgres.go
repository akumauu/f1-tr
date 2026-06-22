package database

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/jackc/pgx"

	"f1tr/internal/config"
)

// Pool wraps the PostgreSQL connection pool used by the rest of the app.
type Pool struct {
	inner *pgx.ConnPool
}

func (p *Pool) Query(ctx context.Context, sql string, args ...interface{}) (*pgx.Rows, error) {
	return p.inner.QueryEx(ctx, sql, nil, args...)
}

func (p *Pool) Exec(ctx context.Context, sql string, args ...interface{}) (pgx.CommandTag, error) {
	return p.inner.ExecEx(ctx, sql, nil, args...)
}

func (p *Pool) Close() {
	p.inner.Close()
}

// DB wraps a pgx connection pool.
type DB struct {
	Pool *Pool
}

// New creates a new database connection pool.
func New(cfg config.DatabaseConfig) (*DB, error) {
	connCfg, err := pgx.ParseURI(cfg.URL)
	if err != nil {
		return nil, fmt.Errorf("parse database url: %w", err)
	}

	poolCfg := pgx.ConnPoolConfig{
		ConnConfig:     connCfg,
		MaxConnections: 10,
		AcquireTimeout: 10 * time.Second,
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	pool, err := pgx.NewConnPool(poolCfg)
	if err != nil {
		return nil, fmt.Errorf("create pool: %w", err)
	}

	if _, err := pool.ExecEx(ctx, "select 1", nil); err != nil {
		return nil, fmt.Errorf("ping database: %w", err)
	}

	log.Println("[DB] Connected to PostgreSQL")
	return &DB{Pool: &Pool{inner: pool}}, nil
}

// Close shuts down the connection pool.
func (db *DB) Close() {
	db.Pool.Close()
	log.Println("[DB] Connection pool closed")
}

// RunMigrations executes all SQL migration files in order.
func (db *DB) RunMigrations(ctx context.Context, migrationsDir string) error {
	files := []string{
		migrationsDir + "/001_create_tables.up.sql",
		migrationsDir + "/002_create_views.up.sql",
	}

	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			return fmt.Errorf("read migration %s: %w", file, err)
		}
		if _, err := db.Pool.Exec(ctx, string(data)); err != nil {
			return fmt.Errorf("execute migration %s: %w", file, err)
		}
		log.Printf("[DB] Applied migration: %s", file)
	}

	return nil
}
