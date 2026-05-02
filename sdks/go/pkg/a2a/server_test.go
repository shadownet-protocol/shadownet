// SPDX-License-Identifier: MIT

package a2a_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/a2a"
	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// presMinter is a small PresentationMinter for the test: bundles a fixed
// credential (and optional freshness) into a fresh VP per call.
type presMinter struct {
	holderKP crypto.KeyPair
	holder   string
	holderID string // holder DID URL with key fragment
	creds    []string
}

func (p *presMinter) Mint(_ context.Context, audience, nonce string, iat, exp time.Time) (string, error) {
	return vc.IssuePresentation(p.holderKP, p.holder, p.holderID, audience, nonce, p.creds, iat, exp)
}

func issueL1(t *testing.T, scaKP crypto.KeyPair, scaDID, scaKID, holderDID string, now time.Time) string {
	t.Helper()
	jwt, err := vc.IssueCredential(scaKP, vc.Credential{
		Issuer: scaDID, Subject: holderDID, JTI: "urn:uuid:test-1",
		IssuedAt: now, Expires: now.Add(48 * time.Hour),
		Level: vc.LevelL1, SubjectType: vc.SubjectPerson,
	}, vc.IssueOptions{IssuerKeyID: scaKID})
	if err != nil {
		t.Fatalf("IssueCredential: %v", err)
	}
	return jwt
}

func TestA2AHandshakeAndMessageFlow(t *testing.T) {
	// SCA, caller, callee identities.
	scaKP, _ := crypto.Generate()
	scaDID, _ := did.EncodeKey(scaKP.Public)
	scaKID := scaDID + "#" + strings.TrimPrefix(scaDID, "did:key:")

	callerKP, _ := crypto.Generate()
	callerDID, _ := did.EncodeKey(callerKP.Public)
	callerKID := callerDID + "#" + strings.TrimPrefix(callerDID, "did:key:")

	calleeKP, _ := crypto.Generate()
	calleeDID, _ := did.EncodeKey(calleeKP.Public)
	calleeKID := calleeDID + "#" + strings.TrimPrefix(calleeDID, "did:key:")

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	credJWT := issueL1(t, scaKP, scaDID, scaKID, callerDID, now)

	// Build the callee's server.
	tasks := a2a.NewMemoryTaskStore()
	verifier := &vc.Verifier{
		Resolver: did.NewKeyResolver(),
		TrustStore: vc.NewMemoryTrustStore([]vc.TrustEntry{{
			Issuer: scaDID, AcceptedLevels: []string{vc.LevelL1, vc.LevelL2},
		}}),
		FreshnessWindow: 24 * time.Hour,
		Now:             func() time.Time { return now.Add(time.Minute) },
	}

	handler := func(ctx context.Context, c a2a.InboundCaller, msg a2a.Message) (a2a.Task, error) {
		// Simple echo: persist a Task in submitted state.
		task := a2a.Task{
			ID: "task-" + msg.MessageID,
			Status: a2a.TaskStatus{
				State:     a2a.TaskSubmitted,
				Timestamp: time.Now().UTC(),
			},
			History: []a2a.Message{msg},
		}
		_ = tasks.Put(ctx, task)
		return task, nil
	}

	server := &a2a.Server{
		DID:         calleeDID,
		KeyID:       calleeKID,
		Key:         calleeKP,
		DIDResolver: did.NewKeyResolver(),
		Verifier:    verifier,
		Tasks:       tasks,
		Handler:     handler,
		Card:        a2a.CardOptions{Name: "callee", URL: "https://callee.example/a2a"},
		Now:         func() time.Time { return now.Add(time.Minute) },
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.HTTPHandler())
	defer srv.Close()

	// Build the caller client.
	minter := &presMinter{holderKP: callerKP, holder: callerDID, holderID: callerKID, creds: []string{credJWT}}
	client := a2a.NewClient(a2a.Identity{DID: callerDID, KeyID: callerKID, Key: callerKP}, minter)
	client.HTTPClient = srv.Client()
	client.Now = func() time.Time { return now.Add(time.Minute) }

	peer := a2a.PeerEndpoint{URL: srv.URL, DID: calleeDID}

	// 1) Build an envelope and send it.
	env, err := a2a.EnvelopePart(a2a.Envelope{
		Version:     "0.1",
		IntentID:    "urn:uuid:int-001",
		Interaction: "urn:shadownet:int:scheduling.v0-draft",
		Payload:     json.RawMessage(`{"kind":"propose"}`),
	})
	if err != nil {
		t.Fatalf("EnvelopePart: %v", err)
	}
	msg := a2a.Message{
		Role:      a2a.RoleUser,
		MessageID: "msg-1",
		Parts:     []a2a.Part{env},
	}
	task, err := client.SendMessage(context.Background(), peer, msg)
	if err != nil {
		t.Fatalf("SendMessage: %v", err)
	}
	if task.Status.State != a2a.TaskSubmitted {
		t.Fatalf("state = %q, want submitted", task.Status.State)
	}

	// 2) task:get
	got, err := client.GetTask(context.Background(), peer, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.ID != task.ID {
		t.Fatalf("id mismatch")
	}

	// 3) task:cancel
	canceled, err := client.CancelTask(context.Background(), peer, task.ID)
	if err != nil {
		t.Fatalf("CancelTask: %v", err)
	}
	if canceled.Status.State != a2a.TaskCanceled {
		t.Fatalf("state = %q, want canceled", canceled.Status.State)
	}
}

func TestA2APresentationRequiredOnFirstRequestWithoutHeader(t *testing.T) {
	// We test the server raw 401 envelope by sending a session token but no VP
	// header on a fresh peer; the server must respond with presentation_required + nonce.
	scaKP, _ := crypto.Generate()
	scaDID, _ := did.EncodeKey(scaKP.Public)

	callerKP, _ := crypto.Generate()
	callerDID, _ := did.EncodeKey(callerKP.Public)
	callerKID := callerDID + "#" + strings.TrimPrefix(callerDID, "did:key:")

	calleeKP, _ := crypto.Generate()
	calleeDID, _ := did.EncodeKey(calleeKP.Public)
	calleeKID := calleeDID + "#" + strings.TrimPrefix(calleeDID, "did:key:")

	now := time.Now().UTC()
	server := &a2a.Server{
		DID: calleeDID, KeyID: calleeKID, Key: calleeKP,
		DIDResolver: did.NewKeyResolver(),
		Verifier: &vc.Verifier{
			Resolver:   did.NewKeyResolver(),
			TrustStore: vc.NewMemoryTrustStore([]vc.TrustEntry{{Issuer: scaDID, AcceptedLevels: []string{vc.LevelL1}}}),
		},
		Tasks: a2a.NewMemoryTaskStore(),
		Handler: func(_ context.Context, _ a2a.InboundCaller, _ a2a.Message) (a2a.Task, error) {
			return a2a.Task{ID: "x"}, nil
		},
		Card: a2a.CardOptions{Name: "callee", URL: "https://x"},
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.HTTPHandler())
	defer srv.Close()

	// Mint session token but pass nil minter so the client sends NO VP header.
	id := a2a.Identity{DID: callerDID, KeyID: callerKID, Key: callerKP}
	client := a2a.NewClient(id, nil)
	client.HTTPClient = srv.Client()
	client.Now = func() time.Time { return now }
	_, err := client.SendMessage(context.Background(), a2a.PeerEndpoint{URL: srv.URL, DID: calleeDID},
		a2a.Message{Role: a2a.RoleUser, MessageID: "x", Parts: nil})
	var aerr *a2a.Error
	if !errors.As(err, &aerr) || aerr.Code != a2a.CodePresentationRequired {
		t.Fatalf("expected presentation_required, got %v", err)
	}
}

func TestA2AAgentCard(t *testing.T) {
	calleeKP, _ := crypto.Generate()
	calleeDID, _ := did.EncodeKey(calleeKP.Public)
	calleeKID := calleeDID + "#" + strings.TrimPrefix(calleeDID, "did:key:")
	server := &a2a.Server{
		DID: calleeDID, KeyID: calleeKID, Key: calleeKP,
		DIDResolver: did.NewKeyResolver(),
		Verifier:    &vc.Verifier{Resolver: did.NewKeyResolver(), TrustStore: vc.NewMemoryTrustStore(nil)},
		Tasks:       a2a.NewMemoryTaskStore(),
		Handler:     func(_ context.Context, _ a2a.InboundCaller, _ a2a.Message) (a2a.Task, error) { return a2a.Task{}, nil },
		Card:        a2a.CardOptions{Name: "Bob's Shadow", URL: "https://bob.example/a2a"},
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.HTTPHandler())
	defer srv.Close()
	resp, err := srv.Client().Get(srv.URL + a2a.AgentCardPath)
	if err != nil {
		t.Fatalf("GET agent-card: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	var card a2a.AgentCard
	if err := json.NewDecoder(resp.Body).Decode(&card); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if card.DID != calleeDID || card.Name != "Bob's Shadow" {
		t.Fatalf("card mismatch: %+v", card)
	}
}

func TestEnvelopeRoundtrip(t *testing.T) {
	in := a2a.Envelope{
		Version:     "0.1",
		IntentID:    "urn:uuid:1",
		Interaction: "urn:shadownet:int:scheduling.v0-draft",
		Payload:     json.RawMessage(`{"kind":"propose","city":"Berlin"}`),
	}
	part, err := a2a.EnvelopePart(in)
	if err != nil {
		t.Fatalf("EnvelopePart: %v", err)
	}
	got, ok, err := a2a.FindEnvelope([]a2a.Part{part})
	if err != nil || !ok {
		t.Fatalf("FindEnvelope: ok=%v err=%v", ok, err)
	}
	if got.IntentID != in.IntentID || got.Interaction != in.Interaction {
		t.Fatalf("envelope roundtrip mismatch")
	}
}
