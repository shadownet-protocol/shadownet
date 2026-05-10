// SPDX-License-Identifier: MIT

package cli

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"flag"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

// Doctor implements `shadownet doctor`. It performs a few sanity checks
// useful when standing up a new deployment:
//   - exercise Ed25519 + JWS sign/verify
//   - if --sca is set, fetch its DID document and policy
//   - if --sns is set, fetch its DID document
//
// Output is plain text; exit code 0 means all configured checks passed.
func Doctor(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	fs.SetOutput(stderr)
	scaURL := fs.String("sca", "", "SCA base URL (no trailing slash) — checks /.well-known/did.json + /.well-known/sca/policy.json")
	snsURL := fs.String("sns", "", "SNS base URL (no trailing slash) — checks /.well-known/did.json")
	if err := fs.Parse(args); err != nil {
		return err
	}

	pass := 0
	fail := 0

	check := func(name string, err error) {
		if err == nil {
			fmt.Fprintf(stdout, "  ok   %s\n", name)
			pass++
		} else {
			fmt.Fprintf(stdout, "  fail %s: %v\n", name, err)
			fail++
		}
	}

	// 1) Crypto roundtrip
	kp, err := crypto.Generate()
	if err != nil {
		check("crypto.Generate", err)
	} else {
		sig := kp.Sign([]byte("doctor"))
		if !crypto.Verify(kp.Public, []byte("doctor"), sig) {
			check("crypto.Verify", fmt.Errorf("signature did not verify"))
		} else {
			check("ed25519 sign/verify", nil)
		}

		jws, err := crypto.SignJWS(kp.Private, []byte(`{"x":1}`), crypto.SignerOptions{KeyID: "did:key:zDoctor#1"})
		if err != nil {
			check("JWS sign", err)
		} else {
			_, body, err := crypto.VerifyJWS(kp.Public, jws)
			if err != nil || !bytes.Equal(body, []byte(`{"x":1}`)) {
				check("JWS verify", fmt.Errorf("payload roundtrip failed: %v", err))
			} else {
				check("JWS roundtrip", nil)
			}
		}

		if len(kp.Public) != ed25519.PublicKeySize {
			check("public key size", fmt.Errorf("got %d, want %d", len(kp.Public), ed25519.PublicKeySize))
		}
	}

	client := &http.Client{Timeout: 10 * time.Second}
	ctx := context.Background()

	if *scaURL != "" {
		check("GET "+*scaURL+"/.well-known/did.json", reachable(ctx, client, *scaURL+"/.well-known/did.json"))
		check("GET "+*scaURL+"/.well-known/sca/policy.json", reachable(ctx, client, *scaURL+"/.well-known/sca/policy.json"))
	}
	if *snsURL != "" {
		check("GET "+*snsURL+"/.well-known/did.json", reachable(ctx, client, *snsURL+"/.well-known/did.json"))
	}

	fmt.Fprintf(stdout, "\nresult: %d passed, %d failed\n", pass, fail)
	if fail > 0 {
		return fmt.Errorf("%d checks failed", fail)
	}
	return nil
}

func reachable(ctx context.Context, c *http.Client, url string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	return nil
}
