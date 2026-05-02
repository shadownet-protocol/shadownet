// SPDX-License-Identifier: MIT

package httpx

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log"
	"log/slog"
	"net/http"
	"runtime/debug"
	"time"
)

type requestIDKey struct{}

// HeaderRequestID is the header name on which a server-issued request id is
// returned to clients. If the inbound request already carries this header,
// we trust it (it lets reverse proxies thread their own id through).
const HeaderRequestID = "X-Request-Id"

// RequestID middleware ensures every request has an X-Request-Id header,
// stamps the value on the response, and stashes it in the request context.
func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get(HeaderRequestID)
		if id == "" {
			id = newRequestID()
		}
		w.Header().Set(HeaderRequestID, id)
		ctx := context.WithValue(r.Context(), requestIDKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// RequestIDFromContext returns the request id stamped by RequestID, or "".
func RequestIDFromContext(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}

// AccessLog records one structured log line per HTTP request.
func AccessLog(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: 200}
			next.ServeHTTP(rec, r)
			logger.LogAttrs(r.Context(), slog.LevelInfo, "http",
				slog.String("method", r.Method),
				slog.String("path", r.URL.Path),
				slog.Int("status", rec.status),
				slog.Duration("dur", time.Since(start)),
				slog.String("remote", r.RemoteAddr),
				slog.String("rid", RequestIDFromContext(r.Context())),
			)
		})
	}
}

// Recover middleware turns panics into 500 responses and logs the stack.
func Recover(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rv := recover(); rv != nil {
					logger.LogAttrs(r.Context(), slog.LevelError, "panic",
						slog.Any("value", rv),
						slog.String("rid", RequestIDFromContext(r.Context())),
						slog.String("stack", string(debug.Stack())),
					)
					http.Error(w, "internal server error", http.StatusInternalServerError)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

type statusRecorder struct {
	http.ResponseWriter
	status      int
	wroteHeader bool
}

func (s *statusRecorder) WriteHeader(code int) {
	if !s.wroteHeader {
		s.status = code
		s.wroteHeader = true
		s.ResponseWriter.WriteHeader(code)
	}
}

func (s *statusRecorder) Write(b []byte) (int, error) {
	if !s.wroteHeader {
		s.WriteHeader(http.StatusOK)
	}
	return s.ResponseWriter.Write(b)
}

func newRequestID() string {
	var b [12]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// slogErrorLog adapts an slog.Logger to *log.Logger so http.Server.ErrorLog
// stays uniform.
func slogErrorLog(logger *slog.Logger) *log.Logger {
	return log.New(&slogWriter{logger: logger}, "", 0)
}

type slogWriter struct{ logger *slog.Logger }

func (w *slogWriter) Write(p []byte) (int, error) {
	w.logger.Error(string(p))
	return len(p), nil
}
