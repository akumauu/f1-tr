package translator

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTranslateRetriesWithFreshBody(t *testing.T) {
	var bodyLengths []int
	attempts := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		bodyLengths = append(bodyLengths, len(body))

		if attempts == 1 {
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		content := `{"transcript_zh":"收到，进站","intent":"STRATEGY","sentiment":"NEUTRAL","key_entities":{"action":"box"}}`
		if err := json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"content": content}},
			},
		}); err != nil {
			t.Fatalf("write response: %v", err)
		}
	}))
	defer server.Close()

	client := NewDeepSeekClient("test-key", server.URL, "test-model")
	client.httpClient = server.Client()

	result, err := client.Translate(context.Background(), "Box this lap", "VER")
	if err != nil {
		t.Fatalf("Translate returned error: %v", err)
	}

	if result.TranscriptZH != "收到，进站" {
		t.Fatalf("translation %q, want 收到，进站", result.TranscriptZH)
	}
	if attempts != 2 {
		t.Fatalf("attempts %d, want 2", attempts)
	}
	if len(bodyLengths) != 2 {
		t.Fatalf("body lengths count %d, want 2", len(bodyLengths))
	}
	for i, n := range bodyLengths {
		if n == 0 {
			t.Fatalf("attempt %d sent empty body", i+1)
		}
	}
}
