package ingester

import (
	"context"
	"fmt"
	"log"

	"golang.org/x/sync/errgroup"
)

// Fetcher coordinates concurrent data fetching from OpenF1.
type Fetcher struct {
	client   *OpenF1Client
	pipeline *Pipeline
}

// NewFetcher creates a new Fetcher.
func NewFetcher(client *OpenF1Client, pipeline *Pipeline) *Fetcher {
	return &Fetcher{client: client, pipeline: pipeline}
}

// FetchMeetingsForYear fetches all meetings for a year, then all sessions within each.
func (f *Fetcher) FetchMeetingsForYear(ctx context.Context, year int) error {
	log.Printf("[Fetcher] Fetching meetings for year %d", year)

	meetings, err := f.client.FetchMeetings(ctx, year)
	if err != nil {
		return fmt.Errorf("fetch meetings: %w", err)
	}
	log.Printf("[Fetcher] Found %d meetings", len(meetings))

	if err := f.pipeline.StoreMeetings(ctx, meetings); err != nil {
		return fmt.Errorf("store meetings: %w", err)
	}

	for _, m := range meetings {
		sessions, err := f.client.FetchSessions(ctx, m.MeetingKey)
		if err != nil {
			log.Printf("[Fetcher] WARN: failed to fetch sessions for meeting %d: %v", m.MeetingKey, err)
			continue
		}
		if err := f.pipeline.StoreSessions(ctx, sessions); err != nil {
			log.Printf("[Fetcher] WARN: failed to store sessions for meeting %d: %v", m.MeetingKey, err)
		}
	}

	return nil
}

// FetchSession fetches all data for a single session concurrently.
func (f *Fetcher) FetchSession(ctx context.Context, sessionKey int) error {
	log.Printf("[Fetcher] Starting concurrent fetch for session %d", sessionKey)

	g, ctx := errgroup.WithContext(ctx)
	g.SetLimit(8) // max 8 concurrent goroutines

	// Drivers
	g.Go(func() error {
		drivers, err := f.client.FetchDrivers(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch drivers: %w", err)
		}
		log.Printf("[Fetcher] Got %d drivers", len(drivers))
		return f.pipeline.StoreDrivers(ctx, drivers)
	})

	// Laps
	g.Go(func() error {
		laps, err := f.client.FetchLaps(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch laps: %w", err)
		}
		log.Printf("[Fetcher] Got %d laps", len(laps))
		return f.pipeline.StoreLaps(ctx, laps)
	})

	// Stints
	g.Go(func() error {
		stints, err := f.client.FetchStints(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch stints: %w", err)
		}
		log.Printf("[Fetcher] Got %d stints", len(stints))
		return f.pipeline.StoreStints(ctx, stints)
	})

	// Team Radio
	g.Go(func() error {
		radios, err := f.client.FetchTeamRadio(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch team radio: %w", err)
		}
		log.Printf("[Fetcher] Got %d team radio messages", len(radios))
		return f.pipeline.StoreTeamRadio(ctx, radios)
	})

	// Pit Stops
	g.Go(func() error {
		pits, err := f.client.FetchPitStops(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch pit stops: %w", err)
		}
		log.Printf("[Fetcher] Got %d pit stops", len(pits))
		return f.pipeline.StorePitStops(ctx, pits)
	})

	// Positions
	g.Go(func() error {
		positions, err := f.client.FetchPositions(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch positions: %w", err)
		}
		log.Printf("[Fetcher] Got %d position records", len(positions))
		return f.pipeline.StorePositions(ctx, positions)
	})

	// Race Control
	g.Go(func() error {
		raceCtl, err := f.client.FetchRaceControl(ctx, sessionKey)
		if err != nil {
			return fmt.Errorf("fetch race control: %w", err)
		}
		log.Printf("[Fetcher] Got %d race control messages", len(raceCtl))
		return f.pipeline.StoreRaceControl(ctx, raceCtl)
	})

	if err := g.Wait(); err != nil {
		return fmt.Errorf("session %d fetch failed: %w", sessionKey, err)
	}

	log.Printf("[Fetcher] Session %d fetch complete ✓", sessionKey)
	return nil
}
