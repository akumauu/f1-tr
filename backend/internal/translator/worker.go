package translator

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"f1tr/internal/database"
)

// Worker processes pending team radio translations in batches.
type Worker struct {
	pool   *database.Pool
	client *DeepSeekClient
	batch  int
}

// NewWorker creates a new translation Worker.
func NewWorker(pool *database.Pool, client *DeepSeekClient, batchSize int) *Worker {
	if batchSize <= 0 {
		batchSize = 10
	}
	return &Worker{pool: pool, client: client, batch: batchSize}
}

type pendingRadio struct {
	ID           int
	TranscriptEN string
	DriverNumber int
	NameAcronym  string
}

// ProcessBatch fetches pending radios and translates them.
func (w *Worker) ProcessBatch(ctx context.Context) (int, error) {
	// Fetch pending records
	rows, err := w.pool.Query(ctx, `
		SELECT tr.id, COALESCE(tr.transcript_en, ''), tr.driver_number, 
		       COALESCE(d.name_acronym, CAST(tr.driver_number AS TEXT))
		FROM team_radio tr
		LEFT JOIN drivers d ON d.session_key = tr.session_key AND d.driver_number = tr.driver_number
		WHERE tr.translation_status = 'pending'
		  AND tr.recording_url != ''
		ORDER BY tr.date ASC
		LIMIT $1
	`, w.batch)
	if err != nil {
		return 0, fmt.Errorf("query pending radios: %w", err)
	}
	defer rows.Close()

	var pending []pendingRadio
	for rows.Next() {
		var p pendingRadio
		if err := rows.Scan(&p.ID, &p.TranscriptEN, &p.DriverNumber, &p.NameAcronym); err != nil {
			return 0, fmt.Errorf("scan row: %w", err)
		}
		pending = append(pending, p)
	}

	if len(pending) == 0 {
		return 0, nil
	}

	log.Printf("[Translator] Processing batch of %d radios", len(pending))

	translated := 0
	for _, p := range pending {
		// Mark as processing
		if _, err := w.pool.Exec(ctx, `
			UPDATE team_radio SET translation_status = 'processing' WHERE id = $1
		`, p.ID); err != nil {
			log.Printf("[Translator] WARN: failed to update status for %d: %v", p.ID, err)
			continue
		}

		// If no English transcript available, use a placeholder note
		transcript := p.TranscriptEN
		if transcript == "" {
			transcript = "[Audio only - no transcript available]"
		}

		result, err := w.client.Translate(ctx, transcript, p.NameAcronym)
		if err != nil {
			log.Printf("[Translator] ERROR: translation failed for radio %d: %v", p.ID, err)
			w.pool.Exec(ctx, `UPDATE team_radio SET translation_status = 'error' WHERE id = $1`, p.ID)
			continue
		}

		// Serialize key_entities to JSON
		entitiesJSON, _ := json.Marshal(result.KeyEntities)

		if _, err := w.pool.Exec(ctx, `
			UPDATE team_radio 
			SET transcript_zh = $1, intent = $2, sentiment = $3, key_entities = $4, translation_status = 'done'
			WHERE id = $5
		`, result.TranscriptZH, result.Intent, result.Sentiment, string(entitiesJSON), p.ID); err != nil {
			log.Printf("[Translator] ERROR: failed to save translation for %d: %v", p.ID, err)
			continue
		}

		translated++
		log.Printf("[Translator] ✓ Radio %d translated (%s)", p.ID, p.NameAcronym)

		// Small delay to avoid rate limits
		time.Sleep(200 * time.Millisecond)
	}

	return translated, nil
}

// Run continuously processes batches until no more pending radios.
func (w *Worker) Run(ctx context.Context) error {
	total := 0
	for {
		select {
		case <-ctx.Done():
			log.Printf("[Translator] Stopped. Total translated: %d", total)
			return ctx.Err()
		default:
		}

		n, err := w.ProcessBatch(ctx)
		if err != nil {
			return fmt.Errorf("process batch: %w", err)
		}
		total += n

		if n == 0 {
			log.Printf("[Translator] No more pending radios. Total translated: %d", total)
			return nil
		}

		log.Printf("[Translator] Batch complete. Running total: %d", total)
	}
}
