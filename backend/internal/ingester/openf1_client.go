package ingester

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"time"
)

const defaultBaseURL = "https://api.openf1.org/v1"

// OpenF1Client is an HTTP client for the OpenF1 API.
type OpenF1Client struct {
	baseURL    string
	httpClient *http.Client
}

// NewOpenF1Client creates a new OpenF1 API client.
func NewOpenF1Client(baseURL string) *OpenF1Client {
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	return &OpenF1Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// fetchJSON sends a GET request and decodes the JSON response into dest.
func (c *OpenF1Client) fetchJSON(ctx context.Context, endpoint string, params map[string]string, dest interface{}) error {
	u, err := url.Parse(c.baseURL + endpoint)
	if err != nil {
		return fmt.Errorf("parse url: %w", err)
	}

	q := u.Query()
	for k, v := range params {
		q.Set(k, v)
	}
	u.RawQuery = q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "application/json")

	// Retry up to 3 times with exponential backoff
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			wait := time.Duration(1<<uint(attempt-1)) * time.Second
			log.Printf("[OpenF1] Retry %d for %s (wait %v)", attempt, endpoint, wait)
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				return ctx.Err()
			}
		}

		resp, err := c.httpClient.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("http request: %w", err)
			continue
		}

		if resp.StatusCode == http.StatusTooManyRequests {
			resp.Body.Close()
			lastErr = fmt.Errorf("rate limited (429)")
			continue
		}

		if resp.StatusCode != http.StatusOK {
			body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
			resp.Body.Close()
			lastErr = fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(body))
			continue
		}

		defer resp.Body.Close()
		if err := json.NewDecoder(resp.Body).Decode(dest); err != nil {
			return fmt.Errorf("decode json: %w", err)
		}
		return nil
	}

	return fmt.Errorf("all retries failed: %w", lastErr)
}

// ---- Raw API response structs (matching OpenF1 JSON) ----

type RawMeeting struct {
	MeetingKey   int     `json:"meeting_key"`
	MeetingName  string  `json:"meeting_name"`
	Location     string  `json:"location"`
	CountryName  string  `json:"country_name"`
	CircuitName  string  `json:"circuit_short_name"`
	Year         int     `json:"year"`
	DateStart    *string `json:"date_start"`
}

type RawSession struct {
	SessionKey  int     `json:"session_key"`
	MeetingKey  int     `json:"meeting_key"`
	SessionName string  `json:"session_name"`
	SessionType string  `json:"session_type"`
	DateStart   *string `json:"date_start"`
	DateEnd     *string `json:"date_end"`
}

type RawDriver struct {
	SessionKey   int    `json:"session_key"`
	DriverNumber int    `json:"driver_number"`
	FullName     string `json:"full_name"`
	NameAcronym  string `json:"name_acronym"`
	TeamName     string `json:"team_name"`
	TeamColour   string `json:"team_colour"`
	HeadshotURL  string `json:"headshot_url"`
	CountryCode  string `json:"country_code"`
}

type RawLap struct {
	SessionKey      int      `json:"session_key"`
	DriverNumber    int      `json:"driver_number"`
	LapNumber       int      `json:"lap_number"`
	LapDuration     *float64 `json:"lap_duration"`
	DurationSector1 *float64 `json:"duration_sector_1"`
	DurationSector2 *float64 `json:"duration_sector_2"`
	DurationSector3 *float64 `json:"duration_sector_3"`
	IsPitOutLap     bool     `json:"is_pit_out_lap"`
	DateStart       *string  `json:"date_start"`
}

type RawStint struct {
	SessionKey      int    `json:"session_key"`
	DriverNumber    int    `json:"driver_number"`
	StintNumber     int    `json:"stint_number"`
	Compound        string `json:"compound"`
	TyreAgeAtStart  *int   `json:"tyre_age_at_start"`
	LapStart        *int   `json:"lap_start"`
	LapEnd          *int   `json:"lap_end"`
}

type RawTeamRadio struct {
	SessionKey   int    `json:"session_key"`
	MeetingKey   int    `json:"meeting_key"`
	DriverNumber int    `json:"driver_number"`
	Date         string `json:"date"`
	RecordingURL string `json:"recording_url"`
}

type RawPit struct {
	SessionKey   int      `json:"session_key"`
	DriverNumber int      `json:"driver_number"`
	LapNumber    int      `json:"lap_number"`
	PitDuration  *float64 `json:"pit_duration"`
	Date         *string  `json:"date"`
}

type RawPosition struct {
	SessionKey   int    `json:"session_key"`
	DriverNumber int    `json:"driver_number"`
	Position     int    `json:"position"`
	Date         string `json:"date"`
}

type RawRaceControl struct {
	SessionKey   int    `json:"session_key"`
	Date         string `json:"date"`
	Category     string `json:"category"`
	Flag         string `json:"flag"`
	Message      string `json:"message"`
	DriverNumber *int   `json:"driver_number"`
	LapNumber    *int   `json:"lap_number"`
}

// ---- Fetch methods ----

func (c *OpenF1Client) FetchMeetings(ctx context.Context, year int) ([]RawMeeting, error) {
	var result []RawMeeting
	params := map[string]string{"year": fmt.Sprintf("%d", year)}
	err := c.fetchJSON(ctx, "/meetings", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchSessions(ctx context.Context, meetingKey int) ([]RawSession, error) {
	var result []RawSession
	params := map[string]string{"meeting_key": fmt.Sprintf("%d", meetingKey)}
	err := c.fetchJSON(ctx, "/sessions", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchDrivers(ctx context.Context, sessionKey int) ([]RawDriver, error) {
	var result []RawDriver
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/drivers", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchLaps(ctx context.Context, sessionKey int) ([]RawLap, error) {
	var result []RawLap
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/laps", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchStints(ctx context.Context, sessionKey int) ([]RawStint, error) {
	var result []RawStint
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/stints", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchTeamRadio(ctx context.Context, sessionKey int) ([]RawTeamRadio, error) {
	var result []RawTeamRadio
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/team_radio", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchPitStops(ctx context.Context, sessionKey int) ([]RawPit, error) {
	var result []RawPit
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/pit", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchPositions(ctx context.Context, sessionKey int) ([]RawPosition, error) {
	var result []RawPosition
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/position", params, &result)
	return result, err
}

func (c *OpenF1Client) FetchRaceControl(ctx context.Context, sessionKey int) ([]RawRaceControl, error) {
	var result []RawRaceControl
	params := map[string]string{"session_key": fmt.Sprintf("%d", sessionKey)}
	err := c.fetchJSON(ctx, "/race_control", params, &result)
	return result, err
}

// ParseTime parses an ISO 8601 timestamp from OpenF1.
func ParseTime(s string) (*time.Time, error) {
	if s == "" {
		return nil, nil
	}
	formats := []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05",
		"2006-01-02T15:04:05.000",
		"2006-01-02",
	}
	for _, f := range formats {
		if t, err := time.Parse(f, s); err == nil {
			return &t, nil
		}
	}
	return nil, fmt.Errorf("cannot parse time: %s", s)
}
