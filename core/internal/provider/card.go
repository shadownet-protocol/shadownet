// SPDX-License-Identifier: MIT

package provider

import (
	"crypto/ed25519"
	"encoding/json"
	"fmt"

	"github.com/shadownet-protocol/shadownet/core/internal/agentcard"
)

// SignCard builds and signs the AgentCard for `r` against the configured
// Provider domain + signing key. The result is the JSON-encoded card body
// ready to write into the HTTP response.
func SignCard(r Record, providerDomain string, signer ed25519.PrivateKey) ([]byte, error) {
	body, err := agentcard.Build(agentcard.Body{
		Name:            displayName(r),
		Description:     description(r, providerDomain),
		Version:         versionOr(r.Version),
		A2AURL:          r.A2AURL,
		ShadowPublicKey: r.ShadowPublicKey,
	})
	if err != nil {
		return nil, fmt.Errorf("provider: build card: %w", err)
	}
	signed, err := agentcard.Sign(body, signer, agentcard.ModeShadowname, providerDomain, r.ShadowPublicKey)
	if err != nil {
		return nil, fmt.Errorf("provider: sign card: %w", err)
	}
	return json.Marshal(signed)
}

func displayName(r Record) string {
	if r.DisplayName != "" {
		return r.DisplayName
	}
	return r.Local
}

func description(r Record, providerDomain string) string {
	if r.Description != "" {
		return r.Description
	}
	return r.Local + "@" + providerDomain
}

func versionOr(v string) string {
	if v == "" {
		return "1.0.0"
	}
	return v
}
