package exporter

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"f1tr/internal/models"
)

type Exporter struct {
	store   Store
	version string
	now     func() time.Time
}

func New(store Store, version string) *Exporter {
	if version == "" {
		version = "v1"
	}
	return &Exporter{
		store:   store,
		version: version,
		now:     func() time.Time { return time.Now().UTC() },
	}
}

func (e *Exporter) ExportAll(ctx context.Context, outDir string) (*Manifest, error) {
	meetings, err := e.store.Meetings(ctx)
	if err != nil {
		return nil, err
	}
	sessions, err := e.store.Sessions(ctx)
	if err != nil {
		return nil, err
	}

	sortMeetings(meetings)
	sortSessions(sessions)

	meetingsByKey := make(map[int]models.Meeting, len(meetings))
	meetingsByYear := make(map[int][]models.Meeting)
	for _, meeting := range meetings {
		meetingsByKey[meeting.MeetingKey] = meeting
		meetingsByYear[meeting.Year] = append(meetingsByYear[meeting.Year], meeting)
	}

	sessionsByMeeting := make(map[int][]models.Session)
	for _, session := range sessions {
		sessionsByMeeting[session.MeetingKey] = append(sessionsByMeeting[session.MeetingKey], session)
	}

	manifest := Manifest{
		Version:           e.version,
		GeneratedAt:       e.now().UTC(),
		Years:             buildYearEntries(meetingsByYear),
		DefaultSessionKey: defaultSessionKey(sessions),
	}

	for _, meeting := range meetings {
		meetingSessions := append([]models.Session(nil), sessionsByMeeting[meeting.MeetingKey]...)
		sortSessions(meetingSessions)

		sessionsPath := fmt.Sprintf("meetings/%d/sessions.json", meeting.MeetingKey)
		if err := writeJSON(outDir, sessionsPath, sliceOrEmpty(meetingSessions)); err != nil {
			return nil, err
		}

		manifest.Meetings = append(manifest.Meetings, MeetingManifest{
			MeetingKey:   meeting.MeetingKey,
			MeetingName:  meeting.MeetingName,
			Location:     meeting.Location,
			CountryName:  meeting.CountryName,
			CircuitName:  meeting.CircuitName,
			Year:         meeting.Year,
			DateStart:    meeting.DateStart,
			SessionsPath: sessionsPath,
			SessionKeys:  sessionKeys(meetingSessions),
		})
	}

	for _, year := range sortedYears(meetingsByYear) {
		yearMeetings := append([]models.Meeting(nil), meetingsByYear[year]...)
		sortMeetings(yearMeetings)
		if err := writeJSON(outDir, fmt.Sprintf("seasons/%d/meetings.json", year), sliceOrEmpty(yearMeetings)); err != nil {
			return nil, err
		}
	}

	for _, session := range sessions {
		meeting, ok := meetingsByKey[session.MeetingKey]
		if !ok {
			return nil, fmt.Errorf("session %d references missing meeting %d", session.SessionKey, session.MeetingKey)
		}
		entry, err := e.exportSession(ctx, outDir, meeting, session)
		if err != nil {
			return nil, err
		}
		manifest.Sessions = append(manifest.Sessions, entry)
	}

	if err := writeJSON(outDir, "manifest.json", manifest); err != nil {
		return nil, err
	}

	return &manifest, nil
}

func (e *Exporter) exportSession(ctx context.Context, outDir string, meeting models.Meeting, session models.Session) (SessionManifest, error) {
	files := make(map[string]FileRef)
	counts := make(map[string]int)

	add := func(dataType string, ref FileRef) {
		files[dataType] = ref
		counts[dataType] = ref.Records
	}

	drivers, err := e.store.Drivers(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err := writeRecords(outDir, sessionFile(session.SessionKey, "drivers.json"), drivers)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeDrivers, ref)

	laps, err := e.store.Laps(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "laps.json"), laps)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeLaps, ref)

	lapDeltas, err := e.store.LapDeltas(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "lap-deltas.json"), lapDeltas)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeLapDeltas, ref)

	stints, err := e.store.Stints(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "stints.json"), stints)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeStints, ref)

	degradation, err := e.store.Degradation(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "degradation.json"), degradation)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeDegradation, ref)

	radio, err := e.store.Radio(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "radio.json"), radio)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeRadio, ref)

	positions, err := e.store.Positions(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "positions.json"), positions)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypePositions, ref)

	pitStops, err := e.store.PitStops(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "pit-stops.json"), pitStops)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypePitStops, ref)

	raceControl, err := e.store.RaceControl(ctx, session.SessionKey)
	if err != nil {
		return SessionManifest{}, err
	}
	ref, err = writeRecords(outDir, sessionFile(session.SessionKey, "race-control.json"), raceControl)
	if err != nil {
		return SessionManifest{}, err
	}
	add(DataTypeRaceControl, ref)

	summaryPath := sessionFile(session.SessionKey, "summary.json")
	summaryRef := FileRef{Path: summaryPath, Records: 1}
	add(DataTypeSummary, summaryRef)

	summary := SessionSummary{
		Meeting:      meeting,
		Session:      session,
		RecordCounts: counts,
		Files:        files,
	}
	if err := writeJSON(outDir, summaryPath, summary); err != nil {
		return SessionManifest{}, err
	}

	return SessionManifest{
		SessionKey:   session.SessionKey,
		MeetingKey:   session.MeetingKey,
		SessionName:  session.SessionName,
		SessionType:  session.SessionType,
		DateStart:    session.DateStart,
		DateEnd:      session.DateEnd,
		Files:        files,
		RecordCounts: counts,
	}, nil
}

func buildYearEntries(meetingsByYear map[int][]models.Meeting) []YearManifest {
	years := sortedYears(meetingsByYear)
	out := make([]YearManifest, 0, len(years))
	for _, year := range years {
		meetings := append([]models.Meeting(nil), meetingsByYear[year]...)
		sortMeetings(meetings)
		out = append(out, YearManifest{
			Year:         year,
			MeetingsPath: fmt.Sprintf("seasons/%d/meetings.json", year),
			MeetingKeys:  meetingKeys(meetings),
		})
	}
	return out
}

func sortedYears(meetingsByYear map[int][]models.Meeting) []int {
	years := make([]int, 0, len(meetingsByYear))
	for year := range meetingsByYear {
		years = append(years, year)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(years)))
	return years
}

func sortMeetings(meetings []models.Meeting) {
	sort.SliceStable(meetings, func(i, j int) bool {
		if meetings[i].Year != meetings[j].Year {
			return meetings[i].Year > meetings[j].Year
		}
		return beforeTimeOrKey(meetings[i].DateStart, meetings[j].DateStart, meetings[i].MeetingKey, meetings[j].MeetingKey)
	})
}

func sortSessions(sessions []models.Session) {
	sort.SliceStable(sessions, func(i, j int) bool {
		return beforeTimeOrKey(sessions[i].DateStart, sessions[j].DateStart, sessions[i].SessionKey, sessions[j].SessionKey)
	})
}

func beforeTimeOrKey(left, right *time.Time, leftKey, rightKey int) bool {
	if left != nil && right != nil && !left.Equal(*right) {
		return left.Before(*right)
	}
	if left == nil && right != nil {
		return false
	}
	if left != nil && right == nil {
		return true
	}
	return leftKey < rightKey
}

func defaultSessionKey(sessions []models.Session) *int {
	var selected *models.Session
	for i := range sessions {
		session := &sessions[i]
		if selected == nil {
			selected = session
			continue
		}
		if isRaceSession(*session) && !isRaceSession(*selected) {
			selected = session
			continue
		}
		if isRaceSession(*session) == isRaceSession(*selected) && afterTimeOrKey(session.DateStart, selected.DateStart, session.SessionKey, selected.SessionKey) {
			selected = session
		}
	}
	if selected == nil {
		return nil
	}
	key := selected.SessionKey
	return &key
}

func afterTimeOrKey(left, right *time.Time, leftKey, rightKey int) bool {
	if left != nil && right != nil && !left.Equal(*right) {
		return left.After(*right)
	}
	if left != nil && right == nil {
		return true
	}
	if left == nil && right != nil {
		return false
	}
	return leftKey > rightKey
}

func isRaceSession(session models.Session) bool {
	name := strings.ToLower(session.SessionName + " " + session.SessionType)
	return strings.Contains(name, "race")
}

func meetingKeys(meetings []models.Meeting) []int {
	out := make([]int, 0, len(meetings))
	for _, meeting := range meetings {
		out = append(out, meeting.MeetingKey)
	}
	return out
}

func sessionKeys(sessions []models.Session) []int {
	out := make([]int, 0, len(sessions))
	for _, session := range sessions {
		out = append(out, session.SessionKey)
	}
	return out
}

func sessionFile(sessionKey int, name string) string {
	return fmt.Sprintf("sessions/%d/%s", sessionKey, name)
}

func writeRecords[T any](outDir, relPath string, records []T) (FileRef, error) {
	records = sliceOrEmpty(records)
	if err := writeJSON(outDir, relPath, records); err != nil {
		return FileRef{}, err
	}
	return FileRef{Path: relPath, Records: len(records)}, nil
}

func sliceOrEmpty[T any](records []T) []T {
	if records == nil {
		return []T{}
	}
	return records
}

func writeJSON(outDir, relPath string, value interface{}) error {
	path := filepath.Join(outDir, filepath.FromSlash(relPath))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create output dir for %s: %w", relPath, err)
	}

	file, err := os.Create(path)
	if err != nil {
		return fmt.Errorf("create %s: %w", relPath, err)
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(value); err != nil {
		return fmt.Errorf("write %s: %w", relPath, err)
	}
	return nil
}
