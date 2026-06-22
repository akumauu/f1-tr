package mimetype

import (
	"bytes"
	"io"
	"net/http"
)

// MIME is the minimal MIME descriptor required by go-playground/validator.
type MIME struct {
	value string
}

func (m *MIME) String() string {
	return m.value
}

// DetectReader detects the MIME type from the first bytes of r.
func DetectReader(r io.Reader) (*MIME, error) {
	var buf bytes.Buffer
	if _, err := io.CopyN(&buf, r, 512); err != nil && err != io.EOF {
		return nil, err
	}
	return &MIME{value: http.DetectContentType(buf.Bytes())}, nil
}
