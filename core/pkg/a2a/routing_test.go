// SPDX-License-Identifier: MIT

package a2a_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/a2a"
	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

func TestRoutingDecisionQuarantineReturns202(t *testing.T) {
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

	tasks := a2a.NewMemoryTaskStore()
	verifier := &vc.Verifier{
		Resolver: did.NewKeyResolver(),
		TrustStore: vc.NewMemoryTrustStore([]vc.TrustEntry{{
			Issuer: scaDID, AcceptedLevels: []string{vc.LevelL1, vc.LevelL2},
		}}),
		FreshnessWindow: 24 * time.Hour,
		Now:             func() time.Time { return now.Add(time.Minute) },
	}

	server := &a2a.Server{
		DID:         calleeDID,
		KeyID:       calleeKID,
		Key:         calleeKP,
		DIDResolver: did.NewKeyResolver(),
		Verifier:    verifier,
		Tasks:       tasks,
		Card:        a2a.CardOptions{Name: "callee", URL: "https://callee.example/a2a"},
		Now:         func() time.Time { return now.Add(time.Minute) },
		Handler: func(_ context.Context, _ a2a.InboundCaller, msg a2a.Message) (a2a.RoutingDecision, a2a.Task, error) {
			return a2a.RouteQuarantine, a2a.Task{
				ID: "q-" + msg.MessageID,
				Status: a2a.TaskStatus{
					State:     a2a.TaskSubmitted,
					Timestamp: now,
				},
				History: []a2a.Message{msg},
			}, nil
		},
	}
	if err := server.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	srv := httptest.NewServer(server.HTTPHandler())
	defer srv.Close()

	minter := &presMinter{holderKP: callerKP, holder: callerDID, holderID: callerKID, creds: []string{credJWT}}
	client := a2a.NewClient(a2a.Identity{DID: callerDID, KeyID: callerKID, Key: callerKP}, minter)
	client.HTTPClient = srv.Client()
	client.Now = func() time.Time { return now.Add(time.Minute) }

	env, err := a2a.EnvelopePart(a2a.Envelope{
		Version:  "0.1",
		IntentID: "urn:uuid:int-q",
		Payload:  json.RawMessage(`{"text":"hi","hints":{"purpose":"invitation"}}`),
	})
	if err != nil {
		t.Fatalf("EnvelopePart: %v", err)
	}
	msg := a2a.Message{Role: a2a.RoleUser, MessageID: "msg-q", Parts: []a2a.Part{env}}
	task, err := client.SendMessage(context.Background(), a2a.PeerEndpoint{URL: srv.URL, DID: calleeDID}, msg)
	if err != nil {
		t.Fatalf("SendMessage: %v", err)
	}
	if task.Status.State != a2a.TaskSubmitted {
		t.Fatalf("quarantined task state = %q, want submitted", task.Status.State)
	}
}

func TestEnvelopeParseHints(t *testing.T) {
	e := a2a.Envelope{
		Version:  "0.1",
		IntentID: "urn:uuid:1",
		Payload:  json.RawMessage(`{"text":"hi","hints":{"purpose":"invitation","proposed_collaboration":"Project Foo","introducer_contact":"did:key:zXyz"}}`),
	}
	p, err := e.ParseFreeForm()
	if err != nil {
		t.Fatalf("ParseFreeForm: %v", err)
	}
	if p.Text != "hi" {
		t.Errorf("Text = %q, want hi", p.Text)
	}
	if p.Hints == nil || p.Hints.Purpose != a2a.PurposeInvitation || p.Hints.ProposedCollaboration != "Project Foo" {
		t.Errorf("Hints mismatch: %+v", p.Hints)
	}
}

func TestPeerDeclinedHTTPStatus(t *testing.T) {
	e := &a2a.Error{Code: a2a.CodePeerDeclined}
	if got, want := e.HTTPStatus(), 403; got != want {
		t.Errorf("peer_declined HTTPStatus = %d, want %d", got, want)
	}
}
