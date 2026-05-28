// SPDX-License-Identifier: MIT

package a2a

import (
	"errors"
	"fmt"
	"net/http"
)

// Error codes per RFC-0006 §Errors.
const (
	CodePresentationRequired = "presentation_required"
	CodePresentationInvalid  = "presentation_invalid"
	CodeLevelInsufficient    = "level_insufficient"
	CodeRevoked              = "revoked"
	CodeFreshnessStale       = "freshness_stale"
	CodeUnknownIntent        = "unknown_intent"
	CodeRateLimited          = "rate_limited"
	CodePeerOffline          = "peer_offline"
	CodePeerDeclined         = "peer_declined"
)

// codeToStatus is the canonical status mapping per RFC-0006.
var codeToStatus = map[string]int{
	CodePresentationRequired: http.StatusUnauthorized,
	CodePresentationInvalid:  http.StatusUnauthorized,
	CodeLevelInsufficient:    http.StatusForbidden,
	CodeRevoked:              http.StatusForbidden,
	CodeFreshnessStale:       http.StatusForbidden,
	CodeUnknownIntent:        http.StatusNotFound,
	CodeRateLimited:          http.StatusTooManyRequests,
	CodePeerOffline:          http.StatusServiceUnavailable,
	CodePeerDeclined:         http.StatusForbidden,
}

// Error is an RFC-0006 error: a stable code, a human-readable detail, and
// an optional nonce (used with presentation_required to bind the next VP).
type Error struct {
	Code   string
	Detail string
	Nonce  string
	Cause  error
}

// New returns a new Error.
func New(code, detail string) *Error { return &Error{Code: code, Detail: detail} }

// HTTPStatus is the canonical HTTP status for the error code.
func (e *Error) HTTPStatus() int {
	if s, ok := codeToStatus[e.Code]; ok {
		return s
	}
	return http.StatusInternalServerError
}

// Error implements the error interface.
func (e *Error) Error() string {
	switch {
	case e.Cause != nil && e.Detail != "":
		return fmt.Sprintf("a2a: %s: %s: %v", e.Code, e.Detail, e.Cause)
	case e.Cause != nil:
		return fmt.Sprintf("a2a: %s: %v", e.Code, e.Cause)
	case e.Detail != "":
		return fmt.Sprintf("a2a: %s: %s", e.Code, e.Detail)
	default:
		return "a2a: " + e.Code
	}
}

// Unwrap allows errors.Is / errors.As to traverse to Cause.
func (e *Error) Unwrap() error { return e.Cause }

// AsError returns err as *Error if it is one (directly or via Unwrap chain).
func AsError(err error) (*Error, bool) {
	var e *Error
	if errors.As(err, &e) {
		return e, true
	}
	return nil, false
}
