package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestParseParamInt(t *testing.T) {
	t.Parallel()
	gin.SetMode(gin.TestMode)

	t.Run("valid", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Params = gin.Params{{Key: "key", Value: "123"}}

		got, ok := parseParamInt(c, "key", "session key")
		if !ok {
			t.Fatal("expected ok")
		}
		if got != 123 {
			t.Fatalf("got %d, want 123", got)
		}
	})

	t.Run("invalid", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Params = gin.Params{{Key: "key", Value: "abc"}}

		_, ok := parseParamInt(c, "key", "session key")
		if ok {
			t.Fatal("expected invalid param")
		}
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d, want %d", w.Code, http.StatusBadRequest)
		}
	})
}

func TestParseOptionalQueryInt(t *testing.T) {
	t.Parallel()
	gin.SetMode(gin.TestMode)

	t.Run("missing", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest(http.MethodGet, "/laps", nil)

		_, present, ok := parseOptionalQueryInt(c, "driver")
		if !ok {
			t.Fatal("expected ok")
		}
		if present {
			t.Fatal("expected missing query")
		}
	})

	t.Run("valid", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest(http.MethodGet, "/laps?driver=44", nil)

		got, present, ok := parseOptionalQueryInt(c, "driver")
		if !ok || !present {
			t.Fatal("expected present query")
		}
		if got != 44 {
			t.Fatalf("got %d, want 44", got)
		}
	})

	t.Run("invalid", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		c.Request = httptest.NewRequest(http.MethodGet, "/laps?driver=ham", nil)

		_, _, ok := parseOptionalQueryInt(c, "driver")
		if ok {
			t.Fatal("expected invalid query")
		}
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d, want %d", w.Code, http.StatusBadRequest)
		}
	})
}

func TestJSONOrNull(t *testing.T) {
	t.Parallel()

	if got := jsonOrNull(nil); got != nil {
		t.Fatalf("empty JSON got %v, want nil", got)
	}

	got := jsonOrNull([]byte(`{"action":"box"}`))
	obj, ok := got.(map[string]interface{})
	if !ok {
		t.Fatalf("got %T, want object", got)
	}
	if obj["action"] != "box" {
		t.Fatalf("action %v, want box", obj["action"])
	}

	if got := jsonOrNull([]byte(`{bad`)); got != "{bad" {
		t.Fatalf("invalid JSON got %v, want raw string", got)
	}
}
