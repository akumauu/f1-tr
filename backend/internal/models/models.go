package models

import "time"

// Meeting represents an F1 race weekend.
type Meeting struct {
	MeetingKey  int        `json:"meeting_key"`
	MeetingName string     `json:"meeting_name"`
	Location    string     `json:"location,omitempty"`
	CountryName string     `json:"country_name,omitempty"`
	CircuitName string     `json:"circuit_name,omitempty"`
	Year        int        `json:"year"`
	DateStart   *time.Time `json:"date_start,omitempty"`
}

// Session represents a single session within a meeting (FP1, Qualifying, Race, etc.).
type Session struct {
	SessionKey  int        `json:"session_key"`
	MeetingKey  int        `json:"meeting_key"`
	SessionName string     `json:"session_name"`
	SessionType string     `json:"session_type,omitempty"`
	DateStart   *time.Time `json:"date_start,omitempty"`
	DateEnd     *time.Time `json:"date_end,omitempty"`
}

// Driver represents a driver in a specific session.
type Driver struct {
	ID           int    `json:"id,omitempty"`
	SessionKey   int    `json:"session_key"`
	DriverNumber int    `json:"driver_number"`
	FullName     string `json:"full_name,omitempty"`
	NameAcronym  string `json:"name_acronym,omitempty"`
	TeamName     string `json:"team_name,omitempty"`
	TeamColour   string `json:"team_colour,omitempty"`
	HeadshotURL  string `json:"headshot_url,omitempty"`
	CountryCode  string `json:"country_code,omitempty"`
}

// Lap represents a single lap record.
type Lap struct {
	ID              int        `json:"id,omitempty"`
	SessionKey      int        `json:"session_key"`
	DriverNumber    int        `json:"driver_number"`
	LapNumber       int        `json:"lap_number"`
	LapDuration     *float64   `json:"lap_duration,omitempty"`
	DurationSector1 *float64   `json:"duration_sector_1,omitempty"`
	DurationSector2 *float64   `json:"duration_sector_2,omitempty"`
	DurationSector3 *float64   `json:"duration_sector_3,omitempty"`
	IsPitOutLap     bool       `json:"is_pit_out_lap"`
	DateStart       *time.Time `json:"date_start,omitempty"`
}

// Stint represents a continuous driving period on one set of tires.
type Stint struct {
	ID             int    `json:"id,omitempty"`
	SessionKey     int    `json:"session_key"`
	DriverNumber   int    `json:"driver_number"`
	StintNumber    int    `json:"stint_number"`
	Compound       string `json:"compound,omitempty"`
	TyreAgeAtStart *int   `json:"tyre_age_at_start,omitempty"`
	LapStart       *int   `json:"lap_start,omitempty"`
	LapEnd         *int   `json:"lap_end,omitempty"`
}

// TeamRadio represents a team radio message.
type TeamRadio struct {
	ID                int                    `json:"id,omitempty"`
	SessionKey        int                    `json:"session_key"`
	MeetingKey        int                    `json:"meeting_key,omitempty"`
	DriverNumber      int                    `json:"driver_number"`
	Date              time.Time              `json:"date"`
	RecordingURL      string                 `json:"recording_url"`
	TranscriptEN      string                 `json:"transcript_en,omitempty"`
	TranscriptZH      string                 `json:"transcript_zh,omitempty"`
	Intent            string                 `json:"intent,omitempty"`
	Sentiment         string                 `json:"sentiment,omitempty"`
	KeyEntities       map[string]interface{} `json:"key_entities,omitempty"`
	TranslationStatus string                 `json:"translation_status,omitempty"`
}

// PitStop represents a pit stop event.
type PitStop struct {
	ID           int        `json:"id,omitempty"`
	SessionKey   int        `json:"session_key"`
	DriverNumber int        `json:"driver_number"`
	LapNumber    int        `json:"lap_number"`
	PitDuration  *float64   `json:"pit_duration,omitempty"`
	Date         *time.Time `json:"date,omitempty"`
}

// Position represents a driver position change.
type Position struct {
	ID           int       `json:"id,omitempty"`
	SessionKey   int       `json:"session_key"`
	DriverNumber int       `json:"driver_number"`
	Position     int       `json:"position"`
	Date         time.Time `json:"date"`
}

// RaceControl represents a race control message.
type RaceControl struct {
	ID           int        `json:"id,omitempty"`
	SessionKey   int        `json:"session_key"`
	Date         time.Time  `json:"date"`
	Category     string     `json:"category,omitempty"`
	Flag         string     `json:"flag,omitempty"`
	Message      string     `json:"message,omitempty"`
	DriverNumber *int       `json:"driver_number,omitempty"`
	LapNumber    *int       `json:"lap_number,omitempty"`
}

// ---- Analytical view models ----

// LapDelta represents a row from v_lap_deltas view.
type LapDelta struct {
	SessionKey       int      `json:"session_key"`
	DriverNumber     int      `json:"driver_number"`
	NameAcronym      string   `json:"name_acronym"`
	TeamName         string   `json:"team_name"`
	LapNumber        int      `json:"lap_number"`
	LapDuration      float64  `json:"lap_duration"`
	Compound         string   `json:"compound,omitempty"`
	StintNumber      *int     `json:"stint_number,omitempty"`
	DeltaToPrev      *float64 `json:"delta_to_prev,omitempty"`
	DeltaToStintStart *float64 `json:"delta_to_stint_start,omitempty"`
}

// TireDegradation represents a row from v_tire_degradation view.
type TireDegradation struct {
	SessionKey      int      `json:"session_key"`
	DriverNumber    int      `json:"driver_number"`
	NameAcronym     string   `json:"name_acronym"`
	TeamName        string   `json:"team_name"`
	StintNumber     int      `json:"stint_number"`
	Compound        string   `json:"compound"`
	LapStart        int      `json:"lap_start"`
	LapEnd          int      `json:"lap_end"`
	StintLength     int      `json:"stint_length"`
	DegRatePerLap   *float64 `json:"deg_rate_sec_per_lap,omitempty"`
	BasePace        *float64 `json:"base_pace,omitempty"`
	RSquared        *float64 `json:"r_squared,omitempty"`
	AvgLapTime      *float64 `json:"avg_lap_time,omitempty"`
	BestLapTime     *float64 `json:"best_lap_time,omitempty"`
}

// RadioWithContext represents a row from v_radio_with_context view.
type RadioWithContext struct {
	ID                int                    `json:"id"`
	SessionKey        int                    `json:"session_key"`
	DriverNumber      int                    `json:"driver_number"`
	NameAcronym       string                 `json:"name_acronym"`
	TeamName          string                 `json:"team_name"`
	RadioTime         time.Time              `json:"radio_time"`
	RecordingURL      string                 `json:"recording_url"`
	TranscriptEN      string                 `json:"transcript_en,omitempty"`
	TranscriptZH      string                 `json:"transcript_zh,omitempty"`
	Intent            string                 `json:"intent,omitempty"`
	Sentiment         string                 `json:"sentiment,omitempty"`
	KeyEntities       map[string]interface{} `json:"key_entities,omitempty"`
	TranslationStatus string                 `json:"translation_status"`
	ApproxLapNumber   *int                   `json:"approx_lap_number,omitempty"`
}
