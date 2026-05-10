// SPDX-License-Identifier: MIT

package scaserver

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sca"
)

// InstantApproval is the method name advertised in policy.json for
// InstantApprovalProofMethod.
const InstantApproval = "instant-approval"

// AllowInstantApprovalEnv is the environment variable a deployment sets to
// run InstantApprovalProofMethod on a non-loopback listener despite the
// startup gate. Documented and audit-friendly.
const AllowInstantApprovalEnv = "SHADOWNET_ALLOW_INSTANT_APPROVAL"

// InstantApprovalProofMethod is the dev-grade ProofMethod the reference
// binaries ship with. Every Start opens a session that is immediately ready —
// no out-of-band proof.
//
// THIS METHOD MUST NOT BE USED IN PRODUCTION. Operators write their own
// ProofMethod implementations satisfying the pkg/sca interface.
type InstantApprovalProofMethod struct{}

// Name implements sca.ProofMethod.
func (InstantApprovalProofMethod) Name() string { return InstantApproval }

// Start implements sca.ProofMethod. Marks the session ready immediately;
// returns a "in-person" NextStep with a noop URL.
func (InstantApprovalProofMethod) Start(_ context.Context, _ sca.Session) (sca.NextStep, *time.Time, error) {
	now := time.Now().UTC()
	return sca.NextStep{Kind: sca.StepInPerson, TTL: 60}, &now, nil
}

// AssertInstantApprovalNotPublic is the startup gate the reference binaries
// run when their policy advertises instant-approval at any level. The check
// is two-tier:
//
//   - listen is loopback: log a Warn so the operator sees it on every boot.
//   - listen is non-loopback AND SHADOWNET_ALLOW_INSTANT_APPROVAL=1 is set:
//     log the same Warn (operator opted in deliberately).
//   - listen is non-loopback AND no opt-in: return an error so the binary
//     refuses to start.
//
// This makes accidental "instant-approval to the open internet" deployments
// impossible without explicit operator action.
func AssertInstantApprovalNotPublic(logger *slog.Logger, listen string, levels []sca.LevelPolicy) error {
	uses := false
	for _, l := range levels {
		if l.Method == InstantApproval {
			uses = true
			break
		}
	}
	if !uses {
		return nil
	}
	allow := os.Getenv(AllowInstantApprovalEnv) == "1"
	if !httpx.IsLoopback(listen) && !allow {
		return fmt.Errorf("instant-approval is configured but listen %q is not loopback; "+
			"this auto-approves every CSR and must not be exposed beyond a trusted network. "+
			"Set %s=1 to opt in for a private test deploy", listen, AllowInstantApprovalEnv)
	}
	if logger == nil {
		logger = slog.Default()
	}
	logger.Warn(
		"InstantApprovalProofMethod is enabled — every /proof/start opens a session that is immediately ready. " +
			"Use this configuration for local development only.",
	)
	return nil
}
