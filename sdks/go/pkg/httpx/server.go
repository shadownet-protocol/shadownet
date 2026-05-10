// SPDX-License-Identifier: MIT

package httpx

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"time"
)

// Hardened HTTP server timeouts. We set them explicitly rather than relying
// on Go's defaults, since net/http has no read timeout out of the box.
const (
	ReadHeaderTimeout = 5 * time.Second
	ReadTimeout       = 10 * time.Second
	WriteTimeout      = 30 * time.Second
	IdleTimeout       = 120 * time.Second
)

// ServerOptions configures NewServer.
type ServerOptions struct {
	Addr      string
	TLSConfig *tls.Config
	Logger    *slog.Logger
}

// NewServer builds an *http.Server with safe defaults: hard timeouts and the
// supplied tls.Config. The handler is wrapped in panic recovery, request-id,
// and access-log middleware.
func NewServer(handler http.Handler, opts ServerOptions) *http.Server {
	if opts.Logger == nil {
		opts.Logger = slog.Default()
	}
	wrapped := Recover(opts.Logger)(RequestID(AccessLog(opts.Logger)(handler)))
	return &http.Server{
		Addr:              opts.Addr,
		Handler:           wrapped,
		TLSConfig:         opts.TLSConfig,
		ReadHeaderTimeout: ReadHeaderTimeout,
		ReadTimeout:       ReadTimeout,
		WriteTimeout:      WriteTimeout,
		IdleTimeout:       IdleTimeout,
		ErrorLog:          slogErrorLog(opts.Logger),
	}
}

// ListenAndServe starts s and returns when ctx is canceled or s fails. If
// TLSConfig is set, it serves TLS; otherwise plaintext (for loopback dev).
//
// On context cancellation, ListenAndServe gracefully shuts down with a
// 15-second deadline.
func ListenAndServe(ctx context.Context, s *http.Server) error {
	errc := make(chan error, 1)
	go func() {
		var err error
		if s.TLSConfig != nil {
			err = s.ListenAndServeTLS("", "")
		} else {
			err = s.ListenAndServe()
		}
		if errors.Is(err, http.ErrServerClosed) {
			err = nil
		}
		errc <- err
	}()
	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		_ = s.Shutdown(shutdownCtx)
		return <-errc
	}
}

// TLSConfig returns a tls.Config that requires TLS 1.3.
func TLSConfig(cert tls.Certificate) *tls.Config {
	return &tls.Config{
		MinVersion:   tls.VersionTLS13,
		Certificates: []tls.Certificate{cert},
	}
}

// IsLoopback reports whether addr (host:port form) binds to a loopback
// address. Empty host, 0.0.0.0, and :: are explicitly NOT loopback — they
// listen on every interface.
func IsLoopback(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		return false
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return host == "localhost"
}

// WarnIfNotLoopback emits a Warn-level log line when addr is non-loopback
// and the deployment lacks TLS. Plaintext HTTP on a public interface is a
// real-world misconfiguration; this helper makes it loud at startup.
//
// hasTLS indicates whether the caller's tls.Config is non-nil; the helper
// returns immediately when TLS is configured.
func WarnIfNotLoopback(logger *slog.Logger, addr string, hasTLS bool) {
	if hasTLS || IsLoopback(addr) {
		return
	}
	if logger == nil {
		logger = slog.Default()
	}
	logger.Warn(
		"plaintext HTTP on a non-loopback address; configure tls.cert and tls.key for production",
		slog.String("listen", addr),
	)
}
