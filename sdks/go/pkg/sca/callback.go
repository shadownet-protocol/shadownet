// SPDX-License-Identifier: MIT

package sca

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"
)

// Caller delivers session state-transition notifications to the operator's
// callbackUrl per RFC-0004 §Callbacks.
//
// Notify is non-blocking — the actual HTTP delivery (including retries on
// failure) MUST run asynchronously so a slow or unreachable callback receiver
// can't stall the SCA's request path. Polling /proof/status remains the
// canonical durable path for clients; callbacks are an optimization.
type Caller interface {
	Notify(ctx context.Context, sess Session, status string)
}

// CallbackBackoff is the retry schedule from RFC-0004 §Callbacks: the first
// attempt is immediate; subsequent attempts after the listed delays. Five
// total attempts; after exhaustion the SCA logs and drops.
var CallbackBackoff = []time.Duration{
	0,
	5 * time.Second,
	30 * time.Second,
	5 * time.Minute,
	30 * time.Minute,
}

// HTTPCaller is the production Caller implementation: HMAC-SHA256-signed POST
// with the spec's retry schedule, fully asynchronous, never blocking the
// caller of Notify.
//
// Zero value is usable: Client defaults to http.Client{Timeout: 10s},
// Logger to slog.Default(), Now to time.Now, Backoff to CallbackBackoff.
type HTTPCaller struct {
	// Client is the HTTP client used for delivery. nil → 10s-timeout default.
	Client *http.Client

	// Logger receives Warn on per-attempt failures, Error on retry exhaustion.
	Logger *slog.Logger

	// Now is the clock for the X-SCA-Callback-Ts header. nil → time.Now.
	Now func() time.Time

	// Backoff overrides CallbackBackoff. nil → CallbackBackoff.
	// Tests pass a tighter schedule (e.g. all-zero) for fast iteration.
	Backoff []time.Duration
}

// Notify implements Caller. Returns immediately; delivery runs in a goroutine.
func (c *HTTPCaller) Notify(ctx context.Context, sess Session, status string) {
	if sess.CallbackURL == "" {
		return
	}
	go c.deliver(ctx, sess, status)
}

func (c *HTTPCaller) deliver(parent context.Context, sess Session, status string) {
	body, err := json.Marshal(map[string]any{
		"shadownet:v": "0.1",
		"sessionId":   sess.ID,
		"status":      status,
	})
	if err != nil {
		c.logger().LogAttrs(parent, slog.LevelError, "sca: callback marshal",
			slog.String("sessionId", sess.ID), slog.String("err", err.Error()))
		return
	}
	schedule := c.Backoff
	if schedule == nil {
		schedule = CallbackBackoff
	}
	for attempt, delay := range schedule {
		if delay > 0 {
			select {
			case <-time.After(delay):
			case <-parent.Done():
				return // server shutting down; abandon
			}
		}
		if err := c.tryOnce(parent, sess, body); err != nil {
			c.logger().LogAttrs(
				parent, slog.LevelWarn, "sca: callback attempt failed",
				slog.String("sessionId", sess.ID),
				slog.Int("attempt", attempt+1),
				slog.String("err", err.Error()),
			)
			continue
		}
		return // success
	}
	c.logger().LogAttrs(
		parent, slog.LevelError, "sca: callback exhausted retries",
		slog.String("sessionId", sess.ID),
		slog.String("url", sess.CallbackURL),
		slog.Int("attempts", len(schedule)),
	)
}

func (c *HTTPCaller) tryOnce(ctx context.Context, sess Session, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, sess.CallbackURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-SCA-Callback-Sig", "sha256="+hmacHex(sess.ID, body))
	req.Header.Set("X-SCA-Callback-Ts", strconv.FormatInt(c.now().Unix(), 10))
	resp, err := c.client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	return fmt.Errorf("status %d", resp.StatusCode)
}

func (c *HTTPCaller) client() *http.Client {
	if c.Client != nil {
		return c.Client
	}
	return &http.Client{Timeout: 10 * time.Second}
}

func (c *HTTPCaller) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now().UTC()
}

func (c *HTTPCaller) logger() *slog.Logger {
	if c.Logger != nil {
		return c.Logger
	}
	return slog.Default()
}

// hmacHex computes HMAC-SHA256(key, body) in lowercase hex.
func hmacHex(key string, body []byte) string {
	h := hmac.New(sha256.New, []byte(key))
	h.Write(body)
	return hex.EncodeToString(h.Sum(nil))
}

// VerifyCallbackSignature is a helper for callback receivers: it returns nil
// when the X-SCA-Callback-Sig header on a delivery matches HMAC-SHA256 of
// body keyed by sessionID.
//
// Receivers SHOULD also reject deliveries whose X-SCA-Callback-Ts differs
// from local time by more than 5 minutes (replay defense per RFC-0004).
func VerifyCallbackSignature(sigHeader, sessionID string, body []byte) error {
	const prefix = "sha256="
	if len(sigHeader) <= len(prefix) || sigHeader[:len(prefix)] != prefix {
		return errors.New("sca: callback sig missing 'sha256=' prefix")
	}
	got, err := hex.DecodeString(sigHeader[len(prefix):])
	if err != nil {
		return fmt.Errorf("sca: callback sig hex decode: %w", err)
	}
	want := hmac.New(sha256.New, []byte(sessionID))
	want.Write(body)
	if !hmac.Equal(got, want.Sum(nil)) {
		return errors.New("sca: callback signature mismatch")
	}
	return nil
}
