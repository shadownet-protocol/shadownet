// SPDX-License-Identifier: MIT

// Package dev implements the development-only AutoApprove ceremony hook.
// It mints credentials for every CSR with no out-of-band verification.
// SUITABLE ONLY FOR LOCAL DEVELOPMENT; the AssertAutoApproveNotPublic
// guard refuses to start the Issuer when this hook is wired into a
// non-loopback listener unless SHADOWNET_ALLOW_AUTO_APPROVE=1 is set.
package dev

import (
	"context"
	"crypto/ed25519"
	"fmt"
	"log/slog"
	"net"
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

// AutoApproveHook is the toy hook that approves every CSR. Use only in
// development.
type AutoApproveHook struct{}

// NewAutoApproveHook returns a fresh AutoApproveHook.
func NewAutoApproveHook() AutoApproveHook { return AutoApproveHook{} }

// Evaluate always returns OutcomeApprove.
func (AutoApproveHook) Evaluate(_ context.Context, _ issuer.CSRView, _ ed25519.PublicKey) (issuer.Decision, error) {
	return issuer.Decision{Outcome: issuer.OutcomeApprove}, nil
}

// AssertAutoApproveNotPublic refuses to start the Issuer with an
// AutoApprove hook on a non-loopback listener unless the operator has
// explicitly opted in via SHADOWNET_ALLOW_AUTO_APPROVE=1. Modeled on the
// v0.1 AssertInstantApprovalNotPublic guard.
func AssertAutoApproveNotPublic(logger *slog.Logger, listenAddr string) error {
	host, _, err := net.SplitHostPort(listenAddr)
	if err != nil {
		host = listenAddr
	}
	if isLoopback(host) {
		return nil
	}
	if os.Getenv("SHADOWNET_ALLOW_AUTO_APPROVE") == "1" {
		if logger != nil {
			logger.Warn("auto-approve hook bound to non-loopback listener; SHADOWNET_ALLOW_AUTO_APPROVE=1 set — DO NOT USE IN PRODUCTION",
				"listen", listenAddr)
		}
		return nil
	}
	return fmt.Errorf("issuer: AutoApprove hook refuses non-loopback listener %q without SHADOWNET_ALLOW_AUTO_APPROVE=1 (DO NOT USE IN PRODUCTION)", listenAddr)
}

func isLoopback(host string) bool {
	if host == "" || host == "localhost" || host == "127.0.0.1" || host == "::1" || host == "[::1]" {
		return true
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	return ip.IsLoopback()
}

// Ensure AutoApproveHook satisfies the Hook interface at compile time.
var _ issuer.Hook = AutoApproveHook{}
