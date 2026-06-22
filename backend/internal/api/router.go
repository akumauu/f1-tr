package api

import (
	"log"
	"net/http"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"f1tr/internal/config"
	"f1tr/internal/database"
	"f1tr/internal/ingester"
	"f1tr/internal/translator"
)

// Server holds all dependencies for the API.
type Server struct {
	pool       *database.Pool
	cfg        *config.Config
	openf1     *ingester.OpenF1Client
	translator *translator.DeepSeekClient
}

// NewServer creates a new API server.
func NewServer(pool *database.Pool, cfg *config.Config) *Server {
	s := &Server{
		pool:   pool,
		cfg:    cfg,
		openf1: ingester.NewOpenF1Client(cfg.OpenF1.BaseURL),
	}
	if cfg.DeepSeek.APIKey != "" {
		s.translator = translator.NewDeepSeekClient(cfg.DeepSeek.APIKey, cfg.DeepSeek.BaseURL, cfg.DeepSeek.Model)
	}
	return s
}

// SetupRouter creates the Gin router with all routes and middleware.
func (s *Server) SetupRouter() *gin.Engine {
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(requestLogger())
	r.Use(cors.New(cors.Config{
		AllowOrigins:     []string{"*"},
		AllowMethods:     []string{"GET", "POST", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Accept"},
		ExposeHeaders:    []string{"Content-Length"},
		AllowCredentials: false,
		MaxAge:           12 * time.Hour,
	}))

	// Health check
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok", "time": time.Now().UTC()})
	})

	v1 := r.Group("/api/v1")
	{
		v1.GET("/meetings", s.handleGetMeetings)
		v1.GET("/meetings/:key/sessions", s.handleGetMeetingSessions)

		v1.GET("/sessions/:key/drivers", s.handleGetDrivers)
		v1.GET("/sessions/:key/laps", s.handleGetLaps)
		v1.GET("/sessions/:key/laps/deltas", s.handleGetLapDeltas)
		v1.GET("/sessions/:key/stints", s.handleGetStints)
		v1.GET("/sessions/:key/degradation", s.handleGetDegradation)
		v1.GET("/sessions/:key/radio", s.handleGetRadio)
		v1.GET("/sessions/:key/positions", s.handleGetPositions)
		v1.GET("/sessions/:key/pit-stops", s.handleGetPitStops)
		v1.GET("/sessions/:key/race-control", s.handleGetRaceControl)

		// Audio proxy for CORS
		v1.GET("/audio-proxy", s.handleAudioProxy)

		// Manual ingest trigger
		v1.POST("/ingest/meetings/:year", s.handleIngestMeetings)
		v1.POST("/ingest/session/:key", s.handleIngestSession)

		// Translation trigger
		v1.POST("/translate/batch", s.handleTranslateBatch)
	}

	return r
}

// requestLogger logs each request with timing.
func requestLogger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		log.Printf("[API] %s %s %d %v",
			c.Request.Method, c.Request.URL.Path,
			c.Writer.Status(), time.Since(start))
	}
}
