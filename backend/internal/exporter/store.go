package exporter

import (
	"context"
	"encoding/json"
	"fmt"

	"f1tr/internal/database"
	"f1tr/internal/models"
)

// Store provides the typed data needed by the static exporter.
type Store interface {
	Meetings(ctx context.Context) ([]models.Meeting, error)
	Sessions(ctx context.Context) ([]models.Session, error)
	Drivers(ctx context.Context, sessionKey int) ([]models.Driver, error)
	Laps(ctx context.Context, sessionKey int) ([]models.Lap, error)
	LapDeltas(ctx context.Context, sessionKey int) ([]models.LapDelta, error)
	Stints(ctx context.Context, sessionKey int) ([]models.Stint, error)
	Degradation(ctx context.Context, sessionKey int) ([]models.TireDegradation, error)
	Radio(ctx context.Context, sessionKey int) ([]models.RadioWithContext, error)
	Positions(ctx context.Context, sessionKey int) ([]models.Position, error)
	PitStops(ctx context.Context, sessionKey int) ([]models.PitStop, error)
	RaceControl(ctx context.Context, sessionKey int) ([]models.RaceControl, error)
}

// PostgresStore reads exporter data from PostgreSQL tables and analytical views.
type PostgresStore struct {
	pool *database.Pool
}

func NewPostgresStore(pool *database.Pool) *PostgresStore {
	return &PostgresStore{pool: pool}
}

func (s *PostgresStore) Meetings(ctx context.Context) ([]models.Meeting, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT meeting_key, COALESCE(meeting_name, ''), COALESCE(location, ''),
		       COALESCE(country_name, ''), COALESCE(circuit_name, ''), year, date_start
		FROM meetings
		ORDER BY year DESC, date_start DESC NULLS LAST, meeting_key DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("query meetings: %w", err)
	}
	defer rows.Close()

	var out []models.Meeting
	for rows.Next() {
		var m models.Meeting
		if err := rows.Scan(&m.MeetingKey, &m.MeetingName, &m.Location, &m.CountryName, &m.CircuitName, &m.Year, &m.DateStart); err != nil {
			return nil, fmt.Errorf("scan meeting: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Sessions(ctx context.Context) ([]models.Session, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT session_key, meeting_key, COALESCE(session_name, ''), COALESCE(session_type, ''),
		       date_start, date_end
		FROM sessions
		ORDER BY date_start DESC NULLS LAST, session_key DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("query sessions: %w", err)
	}
	defer rows.Close()

	var out []models.Session
	for rows.Next() {
		var session models.Session
		if err := rows.Scan(&session.SessionKey, &session.MeetingKey, &session.SessionName, &session.SessionType, &session.DateStart, &session.DateEnd); err != nil {
			return nil, fmt.Errorf("scan session: %w", err)
		}
		out = append(out, session)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Drivers(ctx context.Context, sessionKey int) ([]models.Driver, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, COALESCE(full_name, ''), COALESCE(name_acronym, ''),
		       COALESCE(team_name, ''), COALESCE(team_colour, ''), COALESCE(headshot_url, ''),
		       COALESCE(country_code, '')
		FROM drivers
		WHERE session_key = $1
		ORDER BY driver_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query drivers: %w", err)
	}
	defer rows.Close()

	var out []models.Driver
	for rows.Next() {
		var d models.Driver
		if err := rows.Scan(&d.ID, &d.SessionKey, &d.DriverNumber, &d.FullName, &d.NameAcronym, &d.TeamName, &d.TeamColour, &d.HeadshotURL, &d.CountryCode); err != nil {
			return nil, fmt.Errorf("scan driver: %w", err)
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Laps(ctx context.Context, sessionKey int) ([]models.Lap, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, lap_number, lap_duration,
		       duration_sector_1, duration_sector_2, duration_sector_3,
		       is_pit_out_lap, date_start
		FROM laps
		WHERE session_key = $1
		ORDER BY driver_number, lap_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query laps: %w", err)
	}
	defer rows.Close()

	var out []models.Lap
	for rows.Next() {
		var l models.Lap
		if err := rows.Scan(&l.ID, &l.SessionKey, &l.DriverNumber, &l.LapNumber, &l.LapDuration, &l.DurationSector1, &l.DurationSector2, &l.DurationSector3, &l.IsPitOutLap, &l.DateStart); err != nil {
			return nil, fmt.Errorf("scan lap: %w", err)
		}
		out = append(out, l)
	}
	return out, rows.Err()
}

func (s *PostgresStore) LapDeltas(ctx context.Context, sessionKey int) ([]models.LapDelta, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT session_key, driver_number, COALESCE(name_acronym, ''), COALESCE(team_name, ''),
		       lap_number, lap_duration, COALESCE(compound, ''), stint_number,
		       delta_to_prev, delta_to_stint_start
		FROM v_lap_deltas
		WHERE session_key = $1
		ORDER BY driver_number, lap_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query lap deltas: %w", err)
	}
	defer rows.Close()

	var out []models.LapDelta
	for rows.Next() {
		var row models.LapDelta
		if err := rows.Scan(&row.SessionKey, &row.DriverNumber, &row.NameAcronym, &row.TeamName, &row.LapNumber, &row.LapDuration, &row.Compound, &row.StintNumber, &row.DeltaToPrev, &row.DeltaToStintStart); err != nil {
			return nil, fmt.Errorf("scan lap delta: %w", err)
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Stints(ctx context.Context, sessionKey int) ([]models.Stint, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, stint_number, COALESCE(compound, ''),
		       tyre_age_at_start, lap_start, lap_end
		FROM stints
		WHERE session_key = $1
		ORDER BY driver_number, stint_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query stints: %w", err)
	}
	defer rows.Close()

	var out []models.Stint
	for rows.Next() {
		var st models.Stint
		if err := rows.Scan(&st.ID, &st.SessionKey, &st.DriverNumber, &st.StintNumber, &st.Compound, &st.TyreAgeAtStart, &st.LapStart, &st.LapEnd); err != nil {
			return nil, fmt.Errorf("scan stint: %w", err)
		}
		out = append(out, st)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Degradation(ctx context.Context, sessionKey int) ([]models.TireDegradation, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT session_key, driver_number, COALESCE(name_acronym, ''), COALESCE(team_name, ''),
		       stint_number, COALESCE(compound, ''), lap_start, lap_end, stint_length,
		       deg_rate_sec_per_lap, base_pace, r_squared, avg_lap_time, best_lap_time
		FROM v_tire_degradation
		WHERE session_key = $1
		ORDER BY driver_number, stint_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query degradation: %w", err)
	}
	defer rows.Close()

	var out []models.TireDegradation
	for rows.Next() {
		var row models.TireDegradation
		if err := rows.Scan(&row.SessionKey, &row.DriverNumber, &row.NameAcronym, &row.TeamName, &row.StintNumber, &row.Compound, &row.LapStart, &row.LapEnd, &row.StintLength, &row.DegRatePerLap, &row.BasePace, &row.RSquared, &row.AvgLapTime, &row.BestLapTime); err != nil {
			return nil, fmt.Errorf("scan degradation: %w", err)
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Radio(ctx context.Context, sessionKey int) ([]models.RadioWithContext, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, COALESCE(name_acronym, ''), COALESCE(team_name, ''),
		       radio_time, recording_url, COALESCE(transcript_en, ''), COALESCE(transcript_zh, ''),
		       COALESCE(intent, ''), COALESCE(sentiment, ''), key_entities,
		       COALESCE(translation_status, ''), approx_lap_number
		FROM v_radio_with_context
		WHERE session_key = $1
		ORDER BY radio_time ASC
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query radio: %w", err)
	}
	defer rows.Close()

	var out []models.RadioWithContext
	for rows.Next() {
		var row models.RadioWithContext
		var keyEntities []byte
		if err := rows.Scan(&row.ID, &row.SessionKey, &row.DriverNumber, &row.NameAcronym, &row.TeamName, &row.RadioTime, &row.RecordingURL, &row.TranscriptEN, &row.TranscriptZH, &row.Intent, &row.Sentiment, &keyEntities, &row.TranslationStatus, &row.ApproxLapNumber); err != nil {
			return nil, fmt.Errorf("scan radio: %w", err)
		}
		row.KeyEntities = jsonMapOrNil(keyEntities)
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s *PostgresStore) Positions(ctx context.Context, sessionKey int) ([]models.Position, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, position, date
		FROM positions
		WHERE session_key = $1
		ORDER BY date, driver_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query positions: %w", err)
	}
	defer rows.Close()

	var out []models.Position
	for rows.Next() {
		var p models.Position
		if err := rows.Scan(&p.ID, &p.SessionKey, &p.DriverNumber, &p.Position, &p.Date); err != nil {
			return nil, fmt.Errorf("scan position: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

func (s *PostgresStore) PitStops(ctx context.Context, sessionKey int) ([]models.PitStop, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, driver_number, lap_number, pit_duration, date
		FROM pit_stops
		WHERE session_key = $1
		ORDER BY lap_number, driver_number
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query pit stops: %w", err)
	}
	defer rows.Close()

	var out []models.PitStop
	for rows.Next() {
		var p models.PitStop
		if err := rows.Scan(&p.ID, &p.SessionKey, &p.DriverNumber, &p.LapNumber, &p.PitDuration, &p.Date); err != nil {
			return nil, fmt.Errorf("scan pit stop: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

func (s *PostgresStore) RaceControl(ctx context.Context, sessionKey int) ([]models.RaceControl, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, session_key, date, COALESCE(category, ''), COALESCE(flag, ''),
		       COALESCE(message, ''), driver_number, lap_number
		FROM race_control
		WHERE session_key = $1
		ORDER BY date, id
	`, sessionKey)
	if err != nil {
		return nil, fmt.Errorf("query race control: %w", err)
	}
	defer rows.Close()

	var out []models.RaceControl
	for rows.Next() {
		var r models.RaceControl
		if err := rows.Scan(&r.ID, &r.SessionKey, &r.Date, &r.Category, &r.Flag, &r.Message, &r.DriverNumber, &r.LapNumber); err != nil {
			return nil, fmt.Errorf("scan race control: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func jsonMapOrNil(data []byte) map[string]interface{} {
	if len(data) == 0 {
		return nil
	}
	var out map[string]interface{}
	if err := json.Unmarshal(data, &out); err != nil {
		return map[string]interface{}{"raw": string(data)}
	}
	return out
}
