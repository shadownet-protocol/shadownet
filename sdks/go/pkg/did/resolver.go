// SPDX-License-Identifier: MIT

package did

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
)

// Resolver is the abstraction RFC-0002 describes for "looking up a DID".
//
// At v0.1 there are two implementations: did:key (computed locally) and
// did:web (fetched over HTTPS). NewResolver returns a dispatcher that
// chooses based on the DID method.
type Resolver interface {
	Resolve(ctx context.Context, did string) (*Document, error)
}

// keyResolver is the did:key implementation: pure-local, no network.
type keyResolver struct{}

// NewKeyResolver returns a Resolver that handles did:key only.
func NewKeyResolver() Resolver { return keyResolver{} }

func (keyResolver) Resolve(_ context.Context, did string) (*Document, error) {
	pub, err := DecodeKey(did)
	if err != nil {
		return nil, err
	}
	stripped, _ := SplitDIDURL(did)
	vmID := stripped + "#" + fragmentFromDIDKey(stripped)
	return &Document{
		ID: stripped,
		VerificationMethod: []VerificationMethod{{
			ID:         vmID,
			Controller: stripped,
			Public:     pub,
		}},
		Authentication:  []string{vmID},
		AssertionMethod: []string{vmID},
	}, nil
}

// fragmentFromDIDKey is the canonical fragment for did:key: per W3C the
// verification-method ID is the DID itself with a fragment equal to the
// multibase-encoded body (the part after "did:key:").
func fragmentFromDIDKey(did string) string {
	const prefix = "did:key:"
	if len(did) > len(prefix) {
		return did[len(prefix):]
	}
	return ""
}

// dispatcher routes by DID method. Unknown methods produce ErrUnknownMethod.
type dispatcher struct {
	key Resolver
	web Resolver
}

// ErrUnknownMethod is returned when the DID method is not supported.
var ErrUnknownMethod = errors.New("did: unknown method")

// NewResolver returns a dispatcher that handles did:key locally and forwards
// did:web to web. If web is nil, did:web resolution returns ErrUnknownMethod.
func NewResolver(web Resolver) Resolver {
	return dispatcher{key: NewKeyResolver(), web: web}
}

func (d dispatcher) Resolve(ctx context.Context, did string) (*Document, error) {
	stripped, _ := SplitDIDURL(did)
	switch Method(stripped) {
	case MethodKey:
		return d.key.Resolve(ctx, stripped)
	case MethodWeb:
		if d.web == nil {
			return nil, fmt.Errorf("%w: did:web resolver not configured", ErrUnknownMethod)
		}
		return d.web.Resolve(ctx, stripped)
	default:
		return nil, fmt.Errorf("%w: %q", ErrUnknownMethod, did)
	}
}

// LookupKey resolves a DID URL of the form "did:method:body[#fragment]" and
// returns the public key it identifies.
//
// When the URL has a fragment, the matching verification method is returned.
// When it has no fragment, the first verification method in the resolved
// document is returned (most documents have only one).
func LookupKey(ctx context.Context, r Resolver, didURL string) (ed25519.PublicKey, error) {
	didStr, frag := SplitDIDURL(didURL)
	doc, err := r.Resolve(ctx, didStr)
	if err != nil {
		return nil, err
	}
	if len(doc.VerificationMethod) == 0 {
		return nil, fmt.Errorf("did: %s has no Ed25519 verification methods", didStr)
	}
	if frag == "" {
		return doc.VerificationMethod[0].Public, nil
	}
	if vm, ok := doc.FindVerificationMethod(frag); ok {
		return vm.Public, nil
	}
	if vm, ok := doc.FindVerificationMethod(didURL); ok {
		return vm.Public, nil
	}
	return nil, fmt.Errorf("did: %s has no verification method with id %q", didStr, frag)
}
