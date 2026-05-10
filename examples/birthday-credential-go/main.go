// SPDX-License-Identifier: MIT

// Birthday-credential example.
//
// End-to-end Shadownet credential flow using the Go SDK only: an SCA issues
// a Verifiable Credential to a holder, the holder mints a Verifiable
// Presentation audienced at a peer verifier, and the verifier checks the
// chain. No network, no servers, no DNS — did:key is self-describing.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

func main() {
	if err := run(); err != nil {
		log.Fatalf("example: %v", err)
	}
}

func run() error {
	ctx := context.Background()
	fmt.Println("Shadownet end-to-end credential flow (Go SDK)")
	fmt.Println()

	// 1. Identities -----------------------------------------------------------
	// Three fresh Ed25519 keypairs, each with a self-describing did:key.
	// did:key is recoverable from the DID alone, so no resolver state is
	// required.
	issuerKP, issuerDID, err := newIdentity()
	if err != nil {
		return fmt.Errorf("issuer: %w", err)
	}
	holderKP, holderDID, err := newIdentity()
	if err != nil {
		return fmt.Errorf("holder: %w", err)
	}
	_, verifierDID, err := newIdentity()
	if err != nil {
		return fmt.Errorf("verifier: %w", err)
	}

	fmt.Printf("  SCA / issuer DID: %s\n", issuerDID)
	fmt.Printf("  Holder DID:       %s\n", holderDID)
	fmt.Printf("  Verifier DID:     %s\n", verifierDID)

	resolver := did.NewKeyResolver()

	// 2. Issue & verify the credential ----------------------------------------
	now := time.Now()
	credJWT, err := vc.IssueCredential(
		issuerKP,
		vc.Credential{
			Issuer:      issuerDID,
			Subject:     holderDID,
			JTI:         "urn:uuid:" + randomHex(16),
			IssuedAt:    now,
			Expires:     now.Add(90 * 24 * time.Hour),
			Level:       "urn:shadownet:level:L2",
			SubjectType: vc.SubjectPerson,
		},
		vc.IssueOptions{IssuerKeyID: issuerDID},
	)
	if err != nil {
		return fmt.Errorf("issue credential: %w", err)
	}

	verifiedCred, err := vc.VerifyCredential(ctx, resolver, credJWT, now)
	if err != nil {
		return fmt.Errorf("verify credential: %w", err)
	}
	fmt.Println()
	fmt.Printf("  Issued credential JWT (%d chars).\n", len(credJWT))
	fmt.Printf("  Verified credential: level=%s, sub=%s\n", verifiedCred.Level, verifiedCred.Subject)

	// 3. Mint a Verifiable Presentation ---------------------------------------
	// The VP is signed by the holder, audienced at the verifier, and bundles
	// the credential. RFC-0003 caps VP lifetime at 120 s.
	vpJWT, err := vc.IssuePresentation(
		holderKP,
		holderDID,
		holderDID,
		verifierDID,
		"nonce-"+randomHex(8),
		[]string{credJWT},
		now,
		now.Add(60*time.Second),
	)
	if err != nil {
		return fmt.Errorf("mint presentation: %w", err)
	}
	fmt.Println()
	fmt.Printf("  Minted VP JWT (%d chars).\n", len(vpJWT))

	// 4. Verifier checks the VP ----------------------------------------------
	verifiedVP, err := vc.VerifyPresentation(ctx, resolver, vpJWT, verifierDID, "", now)
	if err != nil {
		return fmt.Errorf("verify presentation: %w", err)
	}
	// VerifyPresentation returns the embedded credential JWTs as raw strings;
	// in a full client flow each would be re-verified against a TrustStore
	// here. We've already verified the credential above, so we just count.
	fmt.Printf("  Verified presentation: holder=%s  audience=%s  credentials=%d\n",
		verifiedVP.Holder, verifiedVP.Audience, len(verifiedVP.Credentials))

	fmt.Println()
	fmt.Println("Done.")
	return nil
}

func newIdentity() (crypto.KeyPair, string, error) {
	kp, err := crypto.Generate()
	if err != nil {
		return crypto.KeyPair{}, "", err
	}
	d, err := did.EncodeKey(kp.Public)
	if err != nil {
		return crypto.KeyPair{}, "", err
	}
	return kp, d, nil
}

func randomHex(nBytes int) string {
	b := make([]byte, nBytes)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
