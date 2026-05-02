// SPDX-License-Identifier: MIT

package sca

import (
	"errors"
	"fmt"
	"net/http"
)

// Error codes from RFC-0004. These are the machine-readable values returned
// in the `error` field of an HTTP error response.
const (
	CodeInvalidLevel    = "invalid_level"
	CodeSubjectBlocked  = "subject_blocked"
	CodeRateLimited     = "rate_limited"
	CodeCSRInvalid      = "csr_invalid"
	CodeSessionMismatch = "session_mismatch"
	CodeSessionNotReady = "session_not_ready"
	CodeSessionConsumed = "session_consumed"
	CodeUnknownJTI      = "unknown_jti"
	CodeRevoked         = "revoked"
	CodeNotHolder       = "not_holder"
	CodeUnauthorized    = "unauthorized"
)

// Sentinel store errors. Implementations of the store interfaces return these
// (or wrap them) so the Issuer can map to RFC-0004 HTTP responses.
var (
	ErrSessionNotFound = errors.New("sca: session not found")
	ErrSessionState    = errors.New("sca: session is not in the required state")
	ErrJTINotFound     = errors.New("sca: credential jti not found")
)

// Error is an RFC-0004 error response: a machine-readable code, optional
// detail, and the HTTP status that the handler should write.
type Error struct {
	HTTPStatus int
	Code       string
	Detail     string
	Cause      error
}

// New returns an *Error with the given fields.
func New(status int, code, detail string) *Error {
	return &Error{HTTPStatus: status, Code: code, Detail: detail}
}

// Wrap returns an *Error with cause attached.
func Wrap(status int, code, detail string, cause error) *Error {
	return &Error{HTTPStatus: status, Code: code, Detail: detail, Cause: cause}
}

// Unauthorized is shorthand for a 401 with code "unauthorized".
func Unauthorized(detail string, cause error) *Error {
	return Wrap(http.StatusUnauthorized, CodeUnauthorized, detail, cause)
}

// Error implements the error interface.
func (e *Error) Error() string {
	switch {
	case e.Cause != nil && e.Detail != "":
		return fmt.Sprintf("sca: %s: %s: %v", e.Code, e.Detail, e.Cause)
	case e.Cause != nil:
		return fmt.Sprintf("sca: %s: %v", e.Code, e.Cause)
	case e.Detail != "":
		return fmt.Sprintf("sca: %s: %s", e.Code, e.Detail)
	default:
		return "sca: " + e.Code
	}
}

// Unwrap allows errors.Is / errors.As to traverse to Cause.
func (e *Error) Unwrap() error { return e.Cause }
