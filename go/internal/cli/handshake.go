// SPDX-License-Identifier: MIT

package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/a2a"
	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/go/pkg/vc"
)

// Handshake implements `shadownet handshake <peer-url>`. It loads a
// keypair and a credential JWT, mints a session token and a VP, sends a
// no-op message:send, and prints the resulting Task.
//
// Useful for smoke tests and as the basis of conformance scenarios.
func Handshake(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("handshake", flag.ContinueOnError)
	fs.SetOutput(stderr)
	keyPath := fs.String("key", "", "path to the holder's private JWK (required)")
	credPath := fs.String("vc", "", "path to a credential JWT for the holder (required)")
	peerDID := fs.String("peer-did", "", "the peer's DID — what the session token's `aud` will be (required)")
	intent := fs.String("intent", "urn:uuid:cli-handshake-1", "envelope intentId")
	interaction := fs.String("interaction", "urn:shadownet:int:smoke.v1", "envelope interaction URI")
	timeout := fs.Duration("timeout", 30*time.Second, "request timeout")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: shadownet handshake --key X --vc Y --peer-did Z <peer-url>")
	}
	if *keyPath == "" || *credPath == "" || *peerDID == "" {
		return errors.New("--key, --vc, --peer-did are required")
	}

	kp, err := crypto.LoadKeyFile(*keyPath)
	if err != nil {
		return fmt.Errorf("load key: %w", err)
	}
	holderDID, err := did.EncodeKey(kp.Public)
	if err != nil {
		return fmt.Errorf("encode did:key: %w", err)
	}
	holderKID := holderDID + "#" + holderDID[len("did:key:"):]

	credBytes, err := os.ReadFile(*credPath)
	if err != nil {
		return fmt.Errorf("read credential: %w", err)
	}
	credJWT := string(bytesTrimSpace(credBytes))

	minter := &cliMinter{kp: kp, holder: holderDID, holderID: holderKID, creds: []string{credJWT}}
	client := a2a.NewClient(a2a.Identity{DID: holderDID, KeyID: holderKID, Key: kp}, minter)

	env, err := a2a.EnvelopePart(a2a.Envelope{
		Version:     "0.1",
		IntentID:    *intent,
		Interaction: *interaction,
		Payload:     json.RawMessage(`{"kind":"smoke"}`),
	})
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	task, err := client.SendMessage(ctx, a2a.PeerEndpoint{URL: fs.Arg(0), DID: *peerDID}, a2a.Message{
		Role:      a2a.RoleUser,
		MessageID: "msg-cli-1",
		Parts:     []a2a.Part{env},
	})
	if err != nil {
		return err
	}

	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(task)
}

type cliMinter struct {
	kp       crypto.KeyPair
	holder   string
	holderID string
	creds    []string
}

func (m *cliMinter) Mint(_ context.Context, audience, nonce string, iat, exp time.Time) (string, error) {
	return vc.IssuePresentation(m.kp, m.holder, m.holderID, audience, nonce, m.creds, iat, exp)
}

func bytesTrimSpace(b []byte) []byte {
	for len(b) > 0 && (b[0] == ' ' || b[0] == '\n' || b[0] == '\r' || b[0] == '\t') {
		b = b[1:]
	}
	for len(b) > 0 && (b[len(b)-1] == ' ' || b[len(b)-1] == '\n' || b[len(b)-1] == '\r' || b[len(b)-1] == '\t') {
		b = b[:len(b)-1]
	}
	return b
}
