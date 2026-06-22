package api

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"f1tr/internal/config"
	"f1tr/internal/ingester"
	"f1tr/internal/translator"
)

const asyncTaskTimeout = 30 * time.Minute

// ---- Meetings & Sessions ----

func (s *Server) handleGetMeetings(c *gin.Context) {
	year, err := strconv.Atoi(c.DefaultQuery("year", "2026"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid year"})
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT meeting_key, meeting_name, location, country_name, circuit_name, year, date_start
		FROM meetings WHERE year = $1 ORDER BY date_start ASC
	`, year)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var mk, yr int
		var name, loc, country, circuit string
		var ds *time.Time
		if err := rows.Scan(&mk, &name, &loc, &country, &circuit, &yr, &ds); err != nil {
			continue
		}
		results = append(results, gin.H{
			"meeting_key": mk, "meeting_name": name, "location": loc,
			"country_name": country, "circuit_name": circuit, "year": yr, "date_start": ds,
		})
	}
	c.JSON(http.StatusOK, results)
}

func (s *Server) handleGetMeetingSessions(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "meeting key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT session_key, meeting_key, session_name, session_type, date_start, date_end
		FROM sessions WHERE meeting_key = $1 ORDER BY date_start ASC
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, mk int
		var name, stype string
		var ds, de *time.Time
		if err := rows.Scan(&sk, &mk, &name, &stype, &ds, &de); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "meeting_key": mk, "session_name": name,
			"session_type": stype, "date_start": ds, "date_end": de,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Drivers ----

func (s *Server) handleGetDrivers(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT DISTINCT ON (driver_number) 
		       driver_number, full_name, name_acronym, team_name, team_colour, headshot_url, country_code
		FROM drivers WHERE session_key = $1 ORDER BY driver_number
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var dn int
		var fn, na, tn, tc, hu, cc string
		if err := rows.Scan(&dn, &fn, &na, &tn, &tc, &hu, &cc); err != nil {
			continue
		}
		results = append(results, gin.H{
			"driver_number": dn, "full_name": fn, "name_acronym": na,
			"team_name": tn, "team_colour": tc, "headshot_url": hu, "country_code": cc,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Laps ----

func (s *Server) handleGetLaps(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}
	driverFilter, hasDriverFilter, ok := parseOptionalQueryInt(c, "driver")
	if !ok {
		return
	}

	query := `
		SELECT session_key, driver_number, lap_number, lap_duration,
		       duration_sector_1, duration_sector_2, duration_sector_3,
		       is_pit_out_lap, date_start
		FROM laps WHERE session_key = $1`
	args := []interface{}{key}

	if hasDriverFilter {
		query += " AND driver_number = $2"
		args = append(args, driverFilter)
	}
	query += " ORDER BY driver_number, lap_number"

	rows, err := s.pool.Query(c.Request.Context(), query, args...)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, ln int
		var ld, ds1, ds2, ds3 *float64
		var isPit bool
		var ds *time.Time
		if err := rows.Scan(&sk, &dn, &ln, &ld, &ds1, &ds2, &ds3, &isPit, &ds); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "lap_number": ln,
			"lap_duration": ld, "duration_sector_1": ds1, "duration_sector_2": ds2,
			"duration_sector_3": ds3, "is_pit_out_lap": isPit, "date_start": ds,
		})
	}
	c.JSON(http.StatusOK, results)
}

func (s *Server) handleGetLapDeltas(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}
	driverFilter, hasDriverFilter, ok := parseOptionalQueryInt(c, "driver")
	if !ok {
		return
	}

	query := `SELECT session_key, driver_number, name_acronym, team_name,
	                 lap_number, lap_duration, compound, stint_number,
	                 delta_to_prev, delta_to_stint_start
	          FROM v_lap_deltas WHERE session_key = $1`
	args := []interface{}{key}

	if hasDriverFilter {
		query += " AND driver_number = $2"
		args = append(args, driverFilter)
	}
	query += " ORDER BY driver_number, lap_number"

	rows, err := s.pool.Query(c.Request.Context(), query, args...)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, ln int
		var na, tn string
		var ld float64
		var comp string
		var sn *int
		var dtp, dtss *float64
		if err := rows.Scan(&sk, &dn, &na, &tn, &ln, &ld, &comp, &sn, &dtp, &dtss); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "name_acronym": na, "team_name": tn,
			"lap_number": ln, "lap_duration": ld, "compound": comp, "stint_number": sn,
			"delta_to_prev": dtp, "delta_to_stint_start": dtss,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Stints & Degradation ----

func (s *Server) handleGetStints(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT session_key, driver_number, stint_number, compound, tyre_age_at_start, lap_start, lap_end
		FROM stints WHERE session_key = $1 ORDER BY driver_number, stint_number
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, sn int
		var comp string
		var tage, ls, le *int
		if err := rows.Scan(&sk, &dn, &sn, &comp, &tage, &ls, &le); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "stint_number": sn,
			"compound": comp, "tyre_age_at_start": tage, "lap_start": ls, "lap_end": le,
		})
	}
	c.JSON(http.StatusOK, results)
}

func (s *Server) handleGetDegradation(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT session_key, driver_number, name_acronym, team_name,
		       stint_number, compound, lap_start, lap_end, stint_length,
		       deg_rate_sec_per_lap, base_pace, r_squared, avg_lap_time, best_lap_time
		FROM v_tire_degradation WHERE session_key = $1
		ORDER BY driver_number, stint_number
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, sn, ls, le, sl int
		var na, tn, comp string
		var dr, bp, r2, alt, blt *float64
		if err := rows.Scan(&sk, &dn, &na, &tn, &sn, &comp, &ls, &le, &sl, &dr, &bp, &r2, &alt, &blt); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "name_acronym": na, "team_name": tn,
			"stint_number": sn, "compound": comp, "lap_start": ls, "lap_end": le,
			"stint_length": sl, "deg_rate_sec_per_lap": dr, "base_pace": bp,
			"r_squared": r2, "avg_lap_time": alt, "best_lap_time": blt,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Team Radio ----

func (s *Server) handleGetRadio(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}
	driverFilter, hasDriverFilter, ok := parseOptionalQueryInt(c, "driver")
	if !ok {
		return
	}

	query := `SELECT id, session_key, driver_number, name_acronym, team_name,
	                 radio_time, recording_url, transcript_en, transcript_zh,
	                 intent, sentiment, key_entities, translation_status, approx_lap_number
	          FROM v_radio_with_context WHERE session_key = $1`
	args := []interface{}{key}

	if hasDriverFilter {
		query += " AND driver_number = $2"
		args = append(args, driverFilter)
	}
	query += " ORDER BY radio_time ASC"

	rows, err := s.pool.Query(c.Request.Context(), query, args...)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var id, sk, dn int
		var na, tn, url, ten, tzh, intent, sentiment, status string
		var rt time.Time
		var ke []byte
		var aln *int
		if err := rows.Scan(&id, &sk, &dn, &na, &tn, &rt, &url, &ten, &tzh,
			&intent, &sentiment, &ke, &status, &aln); err != nil {
			continue
		}
		results = append(results, gin.H{
			"id": id, "session_key": sk, "driver_number": dn,
			"name_acronym": na, "team_name": tn, "radio_time": rt,
			"recording_url": url, "transcript_en": ten, "transcript_zh": tzh,
			"intent": intent, "sentiment": sentiment,
			"key_entities": jsonOrNull(ke), "translation_status": status,
			"approx_lap_number": aln,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Positions, PitStops, RaceControl ----

func (s *Server) handleGetPositions(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	// Return sampled positions (one per lap per driver) to avoid huge datasets
	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT DISTINCT ON (driver_number, position)
		       session_key, driver_number, position, date
		FROM positions WHERE session_key = $1
		ORDER BY driver_number, position, date
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, pos int
		var d time.Time
		if err := rows.Scan(&sk, &dn, &pos, &d); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "position": pos, "date": d,
		})
	}
	c.JSON(http.StatusOK, results)
}

func (s *Server) handleGetPitStops(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT session_key, driver_number, lap_number, pit_duration, date
		FROM pit_stops WHERE session_key = $1 ORDER BY lap_number
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk, dn, ln int
		var pd *float64
		var d *time.Time
		if err := rows.Scan(&sk, &dn, &ln, &pd, &d); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "driver_number": dn, "lap_number": ln,
			"pit_duration": pd, "date": d,
		})
	}
	c.JSON(http.StatusOK, results)
}

func (s *Server) handleGetRaceControl(c *gin.Context) {
	key, ok := parseParamInt(c, "key", "session key")
	if !ok {
		return
	}

	rows, err := s.pool.Query(c.Request.Context(), `
		SELECT session_key, date, category, flag, message, driver_number, lap_number
		FROM race_control WHERE session_key = $1 ORDER BY date
	`, key)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var results []gin.H
	for rows.Next() {
		var sk int
		var d time.Time
		var cat, flag, msg string
		var dn, ln *int
		if err := rows.Scan(&sk, &d, &cat, &flag, &msg, &dn, &ln); err != nil {
			continue
		}
		results = append(results, gin.H{
			"session_key": sk, "date": d, "category": cat, "flag": flag,
			"message": msg, "driver_number": dn, "lap_number": ln,
		})
	}
	c.JSON(http.StatusOK, results)
}

// ---- Audio Proxy (CORS bypass) ----

func (s *Server) handleAudioProxy(c *gin.Context) {
	audioURL := c.Query("url")
	if audioURL == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "url parameter required"})
		return
	}

	resp, err := http.Get(audioURL)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": fmt.Sprintf("fetch audio: %v", err)})
		return
	}
	defer resp.Body.Close()

	contentType := resp.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "audio/mpeg"
	}
	c.Header("Content-Type", contentType)
	c.Header("Cache-Control", "public, max-age=604800")
	c.Header("Access-Control-Allow-Origin", "*")
	c.Status(resp.StatusCode)
	io.Copy(c.Writer, resp.Body)
}

// ---- Ingest Triggers ----

func (s *Server) handleIngestMeetings(c *gin.Context) {
	yearStr := c.Param("year")
	year, err := strconv.Atoi(yearStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid year"})
		return
	}

	pipeline := ingester.NewPipeline(s.pool)
	fetcher := ingester.NewFetcher(s.openf1, pipeline)

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), asyncTaskTimeout)
		defer cancel()
		if err := fetcher.FetchMeetingsForYear(ctx, year); err != nil {
			fmt.Printf("[Ingest] ERROR: %v\n", err)
		}
	}()

	c.JSON(http.StatusAccepted, gin.H{"status": "ingestion started", "year": year})
}

func (s *Server) handleIngestSession(c *gin.Context) {
	keyStr := c.Param("key")
	key, err := strconv.Atoi(keyStr)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid session key"})
		return
	}

	pipeline := ingester.NewPipeline(s.pool)
	fetcher := ingester.NewFetcher(s.openf1, pipeline)

	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), asyncTaskTimeout)
		defer cancel()
		if err := fetcher.FetchSession(ctx, key); err != nil {
			fmt.Printf("[Ingest] ERROR session %d: %v\n", key, err)
		}
	}()

	c.JSON(http.StatusAccepted, gin.H{"status": "session ingestion started", "session_key": key})
}

// ---- Translate Trigger ----

func (s *Server) handleTranslateBatch(c *gin.Context) {
	if s.translator == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "DeepSeek API key not configured"})
		return
	}

	worker := translator.NewWorker(s.pool, s.translator, config.GetBatchSize())
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), asyncTaskTimeout)
		defer cancel()
		if err := worker.Run(ctx); err != nil {
			fmt.Printf("[Translate] ERROR: %v\n", err)
		}
	}()

	c.JSON(http.StatusAccepted, gin.H{"status": "translation started"})
}

// ---- Helpers ----

func parseParamInt(c *gin.Context, name, label string) (int, bool) {
	v, err := strconv.Atoi(c.Param(name))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid " + label})
		return 0, false
	}
	return v, true
}

func parseOptionalQueryInt(c *gin.Context, name string) (int, bool, bool) {
	raw := c.Query(name)
	if raw == "" {
		return 0, false, true
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid " + name})
		return 0, true, false
	}
	return v, true, true
}

func jsonOrNull(b []byte) interface{} {
	if len(b) == 0 {
		return nil
	}
	var v interface{}
	if err := json.Unmarshal(b, &v); err != nil {
		return string(b)
	}
	return v
}
