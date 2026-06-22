package ingester

import (
	"context"
	"fmt"
	"log"

	"f1tr/internal/database"
)

// Pipeline handles data cleaning and batch insertion into PostgreSQL.
type Pipeline struct {
	pool *database.Pool
}

// NewPipeline creates a new Pipeline.
func NewPipeline(pool *database.Pool) *Pipeline {
	return &Pipeline{pool: pool}
}

func (p *Pipeline) StoreMeetings(ctx context.Context, meetings []RawMeeting) error {
	const query = `
		INSERT INTO meetings (meeting_key, meeting_name, location, country_name, circuit_name, year, date_start)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (meeting_key) DO UPDATE SET
			meeting_name = EXCLUDED.meeting_name,
			location = EXCLUDED.location,
			country_name = EXCLUDED.country_name,
			circuit_name = EXCLUDED.circuit_name`

	for _, m := range meetings {
		ts, _ := ParseTime(ptrToStr(m.DateStart))
		if _, err := p.pool.Exec(ctx, query,
			m.MeetingKey, m.MeetingName, m.Location, m.CountryName, m.CircuitName, m.Year, ts,
		); err != nil {
			return fmt.Errorf("insert meeting %d: %w", m.MeetingKey, err)
		}
	}
	log.Printf("[Pipeline] Stored %d meetings", len(meetings))
	return nil
}

func (p *Pipeline) StoreSessions(ctx context.Context, sessions []RawSession) error {
	const query = `
		INSERT INTO sessions (session_key, meeting_key, session_name, session_type, date_start, date_end)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (session_key) DO UPDATE SET
			session_name = EXCLUDED.session_name,
			session_type = EXCLUDED.session_type,
			date_start = EXCLUDED.date_start,
			date_end = EXCLUDED.date_end`

	for _, s := range sessions {
		ds, _ := ParseTime(ptrToStr(s.DateStart))
		de, _ := ParseTime(ptrToStr(s.DateEnd))
		if _, err := p.pool.Exec(ctx, query,
			s.SessionKey, s.MeetingKey, s.SessionName, s.SessionType, ds, de,
		); err != nil {
			return fmt.Errorf("insert session %d: %w", s.SessionKey, err)
		}
	}
	log.Printf("[Pipeline] Stored %d sessions", len(sessions))
	return nil
}

func (p *Pipeline) StoreDrivers(ctx context.Context, drivers []RawDriver) error {
	const query = `
		INSERT INTO drivers (session_key, driver_number, full_name, name_acronym, team_name, team_colour, headshot_url, country_code)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (session_key, driver_number) DO UPDATE SET
			full_name = EXCLUDED.full_name,
			name_acronym = EXCLUDED.name_acronym,
			team_name = EXCLUDED.team_name,
			team_colour = EXCLUDED.team_colour,
			headshot_url = EXCLUDED.headshot_url,
			country_code = EXCLUDED.country_code`

	for _, d := range drivers {
		if _, err := p.pool.Exec(ctx, query,
			d.SessionKey, d.DriverNumber, d.FullName, d.NameAcronym,
			d.TeamName, d.TeamColour, d.HeadshotURL, d.CountryCode,
		); err != nil {
			return fmt.Errorf("insert driver %d: %w", d.DriverNumber, err)
		}
	}
	log.Printf("[Pipeline] Stored %d drivers", len(drivers))
	return nil
}

func (p *Pipeline) StoreLaps(ctx context.Context, laps []RawLap) error {
	const query = `
		INSERT INTO laps (session_key, driver_number, lap_number, lap_duration, duration_sector_1, duration_sector_2, duration_sector_3, is_pit_out_lap, date_start)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		ON CONFLICT (session_key, driver_number, lap_number) DO UPDATE SET
			lap_duration = EXCLUDED.lap_duration,
			duration_sector_1 = EXCLUDED.duration_sector_1,
			duration_sector_2 = EXCLUDED.duration_sector_2,
			duration_sector_3 = EXCLUDED.duration_sector_3,
			is_pit_out_lap = EXCLUDED.is_pit_out_lap`

	batch := 0
	for _, l := range laps {
		ds, _ := ParseTime(ptrToStr(l.DateStart))
		if _, err := p.pool.Exec(ctx, query,
			l.SessionKey, l.DriverNumber, l.LapNumber,
			l.LapDuration, l.DurationSector1, l.DurationSector2, l.DurationSector3,
			l.IsPitOutLap, ds,
		); err != nil {
			return fmt.Errorf("insert lap %d/%d: %w", l.DriverNumber, l.LapNumber, err)
		}
		batch++
	}
	log.Printf("[Pipeline] Stored %d laps", batch)
	return nil
}

func (p *Pipeline) StoreStints(ctx context.Context, stints []RawStint) error {
	const query = `
		INSERT INTO stints (session_key, driver_number, stint_number, compound, tyre_age_at_start, lap_start, lap_end)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (session_key, driver_number, stint_number) DO UPDATE SET
			compound = EXCLUDED.compound,
			tyre_age_at_start = EXCLUDED.tyre_age_at_start,
			lap_start = EXCLUDED.lap_start,
			lap_end = EXCLUDED.lap_end`

	for _, s := range stints {
		if _, err := p.pool.Exec(ctx, query,
			s.SessionKey, s.DriverNumber, s.StintNumber,
			s.Compound, s.TyreAgeAtStart, s.LapStart, s.LapEnd,
		); err != nil {
			return fmt.Errorf("insert stint %d/%d: %w", s.DriverNumber, s.StintNumber, err)
		}
	}
	log.Printf("[Pipeline] Stored %d stints", len(stints))
	return nil
}

func (p *Pipeline) StoreTeamRadio(ctx context.Context, radios []RawTeamRadio) error {
	const query = `
		INSERT INTO team_radio (session_key, meeting_key, driver_number, date, recording_url, translation_status)
		VALUES ($1, $2, $3, $4, $5, 'pending')
		ON CONFLICT (session_key, driver_number, date) DO NOTHING`

	count := 0
	for _, r := range radios {
		ts, err := ParseTime(r.Date)
		if err != nil || ts == nil {
			log.Printf("[Pipeline] WARN: skip radio with bad date: %s", r.Date)
			continue
		}
		if _, err := p.pool.Exec(ctx, query,
			r.SessionKey, r.MeetingKey, r.DriverNumber, ts, r.RecordingURL,
		); err != nil {
			return fmt.Errorf("insert radio: %w", err)
		}
		count++
	}
	log.Printf("[Pipeline] Stored %d team radio messages", count)
	return nil
}

func (p *Pipeline) StorePitStops(ctx context.Context, pits []RawPit) error {
	const query = `
		INSERT INTO pit_stops (session_key, driver_number, lap_number, pit_duration, date)
		VALUES ($1, $2, $3, $4, $5)
		ON CONFLICT (session_key, driver_number, lap_number) DO UPDATE SET
			pit_duration = EXCLUDED.pit_duration`

	for _, pit := range pits {
		ts, _ := ParseTime(ptrToStr(pit.Date))
		if _, err := p.pool.Exec(ctx, query,
			pit.SessionKey, pit.DriverNumber, pit.LapNumber, pit.PitDuration, ts,
		); err != nil {
			return fmt.Errorf("insert pit stop: %w", err)
		}
	}
	log.Printf("[Pipeline] Stored %d pit stops", len(pits))
	return nil
}

func (p *Pipeline) StorePositions(ctx context.Context, positions []RawPosition) error {
	const query = `
		INSERT INTO positions (session_key, driver_number, position, date)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT DO NOTHING`

	// Positions table has no unique constraint for upsert, so we use a batch approach
	// with a temp dedup in Go
	seen := make(map[string]bool)
	count := 0
	for _, pos := range positions {
		key := fmt.Sprintf("%d-%d-%s", pos.SessionKey, pos.DriverNumber, pos.Date)
		if seen[key] {
			continue
		}
		seen[key] = true

		ts, err := ParseTime(pos.Date)
		if err != nil || ts == nil {
			continue
		}
		if _, err := p.pool.Exec(ctx, query,
			pos.SessionKey, pos.DriverNumber, pos.Position, ts,
		); err != nil {
			return fmt.Errorf("insert position: %w", err)
		}
		count++
	}
	log.Printf("[Pipeline] Stored %d positions", count)
	return nil
}

func (p *Pipeline) StoreRaceControl(ctx context.Context, msgs []RawRaceControl) error {
	const query = `
		INSERT INTO race_control (session_key, date, category, flag, message, driver_number, lap_number)
		VALUES ($1, $2, $3, $4, $5, $6, $7)`

	for _, m := range msgs {
		ts, err := ParseTime(m.Date)
		if err != nil || ts == nil {
			continue
		}
		if _, err := p.pool.Exec(ctx, query,
			m.SessionKey, ts, m.Category, m.Flag, m.Message, m.DriverNumber, m.LapNumber,
		); err != nil {
			// Race control may have duplicates; skip
			continue
		}
	}
	log.Printf("[Pipeline] Stored %d race control messages", len(msgs))
	return nil
}

// ptrToStr safely dereferences a *string.
func ptrToStr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
