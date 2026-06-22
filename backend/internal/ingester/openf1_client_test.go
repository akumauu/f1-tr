package ingester

import "testing"

func TestParseTime(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		input   string
		wantNil bool
		wantErr bool
	}{
		{name: "empty", input: "", wantNil: true},
		{name: "rfc3339", input: "2026-06-22T08:00:00Z"},
		{name: "rfc3339 nano", input: "2026-06-22T08:00:00.123456789Z"},
		{name: "no timezone", input: "2026-06-22T08:00:00"},
		{name: "date only", input: "2026-06-22"},
		{name: "invalid", input: "not-a-time", wantErr: true},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got, err := ParseTime(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if (got == nil) != tt.wantNil {
				t.Fatalf("nil mismatch: got %v want nil=%v", got, tt.wantNil)
			}
		})
	}
}
