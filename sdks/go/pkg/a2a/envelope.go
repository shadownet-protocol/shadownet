// SPDX-License-Identifier: MIT

package a2a

import (
	"encoding/json"
	"errors"
	"fmt"
)

// EnvelopePartType is the A2A part `type` Shadownet defines per RFC-0006
// §Message envelope.
const EnvelopePartType = PartShadownetEnvelope

// EnvelopeMediaType is the part's `mediaType` per RFC-0006.
const EnvelopeMediaType = "application/json"

// Envelope is the data of a `shadownet/v1+envelope` part.
type Envelope struct {
	Version     string          `json:"shadownet:v"`
	IntentID    string          `json:"intentId"`
	SessionID   string          `json:"sessionId,omitempty"`
	Interaction string          `json:"interaction"`
	Payload     json.RawMessage `json:"payload"`
}

// Validate checks Envelope shape per RFC-0006.
func (e *Envelope) Validate() error {
	if e.Version != "0.1" {
		return fmt.Errorf("a2a: envelope shadownet:v = %q, want 0.1", e.Version)
	}
	if e.IntentID == "" {
		return errors.New("a2a: envelope intentId required")
	}
	if e.Interaction == "" {
		return errors.New("a2a: envelope interaction required")
	}
	if len(e.Payload) == 0 {
		return errors.New("a2a: envelope payload required")
	}
	return nil
}

// EnvelopePart returns a Part representing this Envelope.
func EnvelopePart(e Envelope) (Part, error) {
	if err := e.Validate(); err != nil {
		return Part{}, err
	}
	body, err := json.Marshal(e)
	if err != nil {
		return Part{}, fmt.Errorf("a2a: marshal envelope: %w", err)
	}
	return Part{
		Type:      EnvelopePartType,
		MediaType: EnvelopeMediaType,
		Data:      body,
	}, nil
}

// FindEnvelope walks parts and returns the first shadownet/v1+envelope.
//
// Returns false when none is present, which is valid: A2A messages may carry
// only Text/File/Data parts; Shadownet's profile MUST NOT reject those.
func FindEnvelope(parts []Part) (Envelope, bool, error) {
	for _, p := range parts {
		if p.Type != EnvelopePartType {
			continue
		}
		var e Envelope
		if err := json.Unmarshal(p.Data, &e); err != nil {
			return Envelope{}, true, fmt.Errorf("a2a: parse envelope: %w", err)
		}
		if err := e.Validate(); err != nil {
			return Envelope{}, true, err
		}
		return e, true, nil
	}
	return Envelope{}, false, nil
}
