package exporter

import (
	"time"

	"f1tr/internal/models"
)

const (
	DataTypeSummary     = "summary"
	DataTypeDrivers     = "drivers"
	DataTypeLaps        = "laps"
	DataTypeLapDeltas   = "lap_deltas"
	DataTypeStints      = "stints"
	DataTypeDegradation = "degradation"
	DataTypeRadio       = "radio"
	DataTypePositions   = "positions"
	DataTypePitStops    = "pit_stops"
	DataTypeRaceControl = "race_control"
)

// Manifest is the public entry point consumed by the frontend.
type Manifest struct {
	Version           string            `json:"version"`
	GeneratedAt       time.Time         `json:"generated_at"`
	Years             []YearManifest    `json:"years"`
	Meetings          []MeetingManifest `json:"meetings"`
	Sessions          []SessionManifest `json:"sessions"`
	DefaultSessionKey *int              `json:"default_session_key,omitempty"`
}

type YearManifest struct {
	Year         int    `json:"year"`
	MeetingsPath string `json:"meetings_path"`
	MeetingKeys  []int  `json:"meeting_keys"`
}

type MeetingManifest struct {
	MeetingKey   int        `json:"meeting_key"`
	MeetingName  string     `json:"meeting_name"`
	Location     string     `json:"location,omitempty"`
	CountryName  string     `json:"country_name,omitempty"`
	CircuitName  string     `json:"circuit_name,omitempty"`
	Year         int        `json:"year"`
	DateStart    *time.Time `json:"date_start,omitempty"`
	SessionsPath string     `json:"sessions_path"`
	SessionKeys  []int      `json:"session_keys"`
}

type SessionManifest struct {
	SessionKey   int                `json:"session_key"`
	MeetingKey   int                `json:"meeting_key"`
	SessionName  string             `json:"session_name"`
	SessionType  string             `json:"session_type,omitempty"`
	DateStart    *time.Time         `json:"date_start,omitempty"`
	DateEnd      *time.Time         `json:"date_end,omitempty"`
	Files        map[string]FileRef `json:"files"`
	RecordCounts map[string]int     `json:"record_counts"`
}

type FileRef struct {
	Path    string `json:"path"`
	Records int    `json:"records"`
}

type SessionSummary struct {
	Meeting      models.Meeting     `json:"meeting"`
	Session      models.Session     `json:"session"`
	RecordCounts map[string]int     `json:"record_counts"`
	Files        map[string]FileRef `json:"files"`
}
