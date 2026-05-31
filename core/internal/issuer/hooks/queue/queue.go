// SPDX-License-Identifier: MIT

// Package queue implements the production-default ceremony Hook. It
// parks every incoming CSR in the Store as a Pending row and surfaces a
// 409 ceremony_pending response with a `next` URL pointing back at the
// same Issuer. The admin CLI (or external tooling that calls Store.Put
// directly) advances Pendings to PendingApproved or PendingRejected;
// the next CSR re-POST observes the new status and either mints the
// credential (200) or returns 403 ceremony_failed.
//
// Re-POSTs of the same logical CSR (same idempotency key) re-use the
// existing Pending and return its current Status — so a client polling
// the Issuer with the same CSR every few minutes will move from 409 →
// (admin approves) → 200, without operator intervention on the wire.
package queue

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

// Config picks how the queue hook constructs its Decisions.
type Config struct {
	// Store is the persistence layer the hook parks ceremonies into.
	Store issuer.Store

	// NextURL is the URL surfaced to the Subject in the 409 response —
	// typically pointing back at the same Issuer's issue endpoint so the
	// Subject's polling loop re-POSTs there.
	NextURL string

	// Now overrides time.Now for deterministic testing.
	Now func() time.Time
}

// Hook is the issuer.Hook implementation.
type Hook struct {
	cfg Config
}

// New returns a Hook configured against the supplied Store + NextURL.
func New(cfg Config) (*Hook, error) {
	if cfg.Store == nil {
		return nil, errors.New("queue: Store required")
	}
	if cfg.NextURL == "" {
		return nil, errors.New("queue: NextURL required")
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	return &Hook{cfg: cfg}, nil
}

// Evaluate parks the CSR (or surfaces the existing Pending) and translates
// the parked state into the matching Decision.
func (h *Hook) Evaluate(ctx context.Context, csr issuer.CSRView, subjectPub ed25519.PublicKey) (issuer.Decision, error) {
	idem, err := issuer.IdempotencyKey(csr.Iss, csr.Aud, map[string]any{
		"kind": csr.Kind,
		"org":  csr.Org,
	})
	if err != nil {
		return issuer.Decision{}, fmt.Errorf("queue: idempotency key: %w", err)
	}

	if existing, err := h.cfg.Store.GetPendingByIdempotencyKey(ctx, idem); err == nil {
		// Re-POST of an already-parked ceremony. Mirror its current
		// state back to the handler.
		return h.decisionForPending(existing, idem), nil
	} else if !errors.Is(err, issuer.ErrNotFound) {
		return issuer.Decision{}, fmt.Errorf("queue: lookup: %w", err)
	}

	pubMB, err := identifiers.EncodePubKey(subjectPub)
	if err != nil {
		return issuer.Decision{}, fmt.Errorf("queue: encode subject pub: %w", err)
	}

	handle, err := newHandleID()
	if err != nil {
		return issuer.Decision{}, fmt.Errorf("queue: new handle: %w", err)
	}
	now := h.cfg.Now()
	pending := issuer.Pending{
		HandleID:       handle,
		IdempotencyKey: idem,
		Iss:            csr.Iss,
		Aud:            csr.Aud,
		Kind:           csr.Kind,
		Org:            csr.Org,
		SubjectPubKey:  pubMB,
		Status:         issuer.PendingNew,
		NextURL:        h.cfg.NextURL,
		CreatedAt:      now,
		UpdatedAt:      now,
		CeremonyExpiry: csr.Expiry,
	}
	if err := h.cfg.Store.PutPending(ctx, pending); err != nil {
		return issuer.Decision{}, fmt.Errorf("queue: park pending: %w", err)
	}
	return issuer.Decision{
		Outcome:        issuer.OutcomePending,
		HandleID:       handle,
		NextURL:        h.cfg.NextURL,
		CeremonyExpiry: csr.Expiry,
	}, nil
}

func (h *Hook) decisionForPending(p issuer.Pending, idem string) issuer.Decision {
	switch p.Status {
	case issuer.PendingApproved:
		return issuer.Decision{
			Outcome:        issuer.OutcomeApprove,
			HandleID:       p.HandleID,
			CeremonyExpiry: p.CeremonyExpiry,
		}
	case issuer.PendingRejected:
		return issuer.Decision{
			Outcome:        issuer.OutcomeReject,
			Reason:         p.Reason,
			HandleID:       p.HandleID,
			CeremonyExpiry: p.CeremonyExpiry,
		}
	default:
		_ = idem // captured for symmetry with future logging.
		return issuer.Decision{
			Outcome:        issuer.OutcomePending,
			HandleID:       p.HandleID,
			NextURL:        p.NextURL,
			CeremonyExpiry: p.CeremonyExpiry,
		}
	}
}

func newHandleID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}

// Ensure Hook satisfies the issuer.Hook interface at compile time.
var _ issuer.Hook = (*Hook)(nil)
