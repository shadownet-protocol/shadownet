// SPDX-License-Identifier: MIT

package main

import (
	"context"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
)

// InstantApproval is the method name advertised in policy.json for
// InstantApprovalProofMethod.
const InstantApproval = "instant-approval"

// InstantApprovalProofMethod is the dev-grade ProofMethod cmd/sca-server
// ships with. Every Start opens a session that is immediately ready — there
// is no out-of-band proof. Useful for local development, conformance suite
// runs, and any operator who wants to bring up an SCA without picking a
// production proof flow yet.
//
// THIS METHOD MUST NOT BE USED IN PRODUCTION. Operators write their own
// ProofMethod implementations satisfying the pkg/sca interface.
type InstantApprovalProofMethod struct{}

// Name implements sca.ProofMethod.
func (InstantApprovalProofMethod) Name() string { return InstantApproval }

// Start implements sca.ProofMethod. The session is marked ready immediately;
// the next step kind is "in-person" with a noop URL — the SCA's caller has
// no further action to take.
func (InstantApprovalProofMethod) Start(_ context.Context, _ sca.Session) (sca.NextStep, *time.Time, error) {
	now := time.Now().UTC()
	return sca.NextStep{Kind: sca.StepInPerson, TTL: 60}, &now, nil
}
