package exporter

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"f1tr/internal/models"
)

type fakeStore struct {
	meetings []models.Meeting
	sessions []models.Session
}

func (f fakeStore) Meetings(context.Context) ([]models.Meeting, error) {
	return f.meetings, nil
}

func (f fakeStore) Sessions(context.Context) ([]models.Session, error) {
	return f.sessions, nil
}

func (f fakeStore) Drivers(context.Context, int) ([]models.Driver, error) {
	return []models.Driver{{SessionKey: 9001, DriverNumber: 1, NameAcronym: "VER", TeamName: "Red Bull Racing"}}, nil
}

func (f fakeStore) Laps(context.Context, int) ([]models.Lap, error) {
	lapDuration := 82.1
	return []models.Lap{{SessionKey: 9001, DriverNumber: 1, LapNumber: 1, LapDuration: &lapDuration}}, nil
}

func (f fakeStore) LapDeltas(context.Context, int) ([]models.LapDelta, error) {
	return []models.LapDelta{{SessionKey: 9001, DriverNumber: 1, NameAcronym: "VER", LapNumber: 1, LapDuration: 82.1}}, nil
}

func (f fakeStore) Stints(context.Context, int) ([]models.Stint, error) {
	lapStart := 1
	lapEnd := 18
	return []models.Stint{{SessionKey: 9001, DriverNumber: 1, StintNumber: 1, Compound: "MEDIUM", LapStart: &lapStart, LapEnd: &lapEnd}}, nil
}

func (f fakeStore) Degradation(context.Context, int) ([]models.TireDegradation, error) {
	rate := 0.08
	return []models.TireDegradation{{SessionKey: 9001, DriverNumber: 1, NameAcronym: "VER", StintNumber: 1, Compound: "MEDIUM", LapStart: 1, LapEnd: 18, StintLength: 18, DegRatePerLap: &rate}}, nil
}

func (f fakeStore) Radio(context.Context, int) ([]models.RadioWithContext, error) {
	return []models.RadioWithContext{{ID: 1, SessionKey: 9001, DriverNumber: 1, NameAcronym: "VER", RadioTime: time.Date(2026, 3, 8, 7, 10, 0, 0, time.UTC), RecordingURL: "https://example.test/radio.mp3", TranscriptEN: "Box box", TranscriptZH: "Pit stop", TranslationStatus: "done"}}, nil
}

func (f fakeStore) Positions(context.Context, int) ([]models.Position, error) {
	return []models.Position{{SessionKey: 9001, DriverNumber: 1, Position: 1, Date: time.Date(2026, 3, 8, 7, 9, 0, 0, time.UTC)}}, nil
}

func (f fakeStore) PitStops(context.Context, int) ([]models.PitStop, error) {
	duration := 2.4
	return []models.PitStop{{SessionKey: 9001, DriverNumber: 1, LapNumber: 18, PitDuration: &duration}}, nil
}

func (f fakeStore) RaceControl(context.Context, int) ([]models.RaceControl, error) {
	return []models.RaceControl{{SessionKey: 9001, Date: time.Date(2026, 3, 8, 7, 8, 0, 0, time.UTC), Category: "Flag", Message: "GREEN LIGHT"}}, nil
}

func TestExportAllWritesManifestAndSessionFiles(t *testing.T) {
	start := time.Date(2026, 3, 8, 7, 0, 0, 0, time.UTC)
	outDir := t.TempDir()
	exp := New(fakeStore{
		meetings: []models.Meeting{{
			MeetingKey:  100,
			MeetingName: "Bahrain Grand Prix",
			CountryName: "Bahrain",
			Year:        2026,
			DateStart:   &start,
		}},
		sessions: []models.Session{{
			SessionKey:  9001,
			MeetingKey:  100,
			SessionName: "Race",
			SessionType: "Race",
			DateStart:   &start,
		}},
	}, "test-v1")
	exp.now = func() time.Time { return start }

	manifest, err := exp.ExportAll(context.Background(), outDir)
	if err != nil {
		t.Fatalf("ExportAll() error = %v", err)
	}

	if manifest.Version != "test-v1" {
		t.Fatalf("manifest version = %q", manifest.Version)
	}
	if manifest.DefaultSessionKey == nil || *manifest.DefaultSessionKey != 9001 {
		t.Fatalf("default session key = %v", manifest.DefaultSessionKey)
	}

	requiredFiles := []string{
		"manifest.json",
		"seasons/2026/meetings.json",
		"meetings/100/sessions.json",
		"sessions/9001/summary.json",
		"sessions/9001/drivers.json",
		"sessions/9001/laps.json",
		"sessions/9001/lap-deltas.json",
		"sessions/9001/stints.json",
		"sessions/9001/degradation.json",
		"sessions/9001/radio.json",
		"sessions/9001/positions.json",
		"sessions/9001/pit-stops.json",
		"sessions/9001/race-control.json",
	}
	for _, rel := range requiredFiles {
		if _, err := os.Stat(filepath.Join(outDir, filepath.FromSlash(rel))); err != nil {
			t.Fatalf("expected %s to exist: %v", rel, err)
		}
	}

	var written Manifest
	data, err := os.ReadFile(filepath.Join(outDir, "manifest.json"))
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	if err := json.Unmarshal(data, &written); err != nil {
		t.Fatalf("decode manifest: %v", err)
	}
	if got := written.Sessions[0].Files[DataTypeLapDeltas].Path; got != "sessions/9001/lap-deltas.json" {
		t.Fatalf("lap delta path = %q", got)
	}
	if got := written.Sessions[0].RecordCounts[DataTypeRadio]; got != 1 {
		t.Fatalf("radio count = %d", got)
	}
}

func TestDefaultSessionPrefersLatestRace(t *testing.T) {
	fp := time.Date(2026, 3, 7, 12, 0, 0, 0, time.UTC)
	oldRace := time.Date(2026, 3, 6, 12, 0, 0, 0, time.UTC)
	latestRace := time.Date(2026, 3, 8, 12, 0, 0, 0, time.UTC)

	key := defaultSessionKey([]models.Session{
		{SessionKey: 1, SessionName: "Practice", DateStart: &fp},
		{SessionKey: 2, SessionName: "Race", DateStart: &oldRace},
		{SessionKey: 3, SessionName: "Grand Prix", SessionType: "Race", DateStart: &latestRace},
	})
	if key == nil || *key != 3 {
		t.Fatalf("defaultSessionKey() = %v", key)
	}
}
