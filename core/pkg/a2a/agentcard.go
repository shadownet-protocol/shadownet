// SPDX-License-Identifier: MIT

package a2a

import (
	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

// AgentCardPath is the well-known location for an A2A agent card.
const AgentCardPath = "/.well-known/agent-card.json"

// AgentCard is the JSON document a Shadow publishes at AgentCardPath.
//
// Required fields per RFC-0006: name, url, did, publicKey, shadownet:v.
// Implementations MAY add others; consumers MUST tolerate unknown fields.
type AgentCard struct {
	Name      string     `json:"name"`
	URL       string     `json:"url"`
	DID       string     `json:"did"`
	PublicKey crypto.JWK `json:"publicKey"`
	Version   string     `json:"shadownet:v"`
}
