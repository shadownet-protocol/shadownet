// SPDX-License-Identifier: MIT

package a2a

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

// MaxClientResponseBytes caps the size of a JSON-RPC response body.
const MaxClientResponseBytes = 1 << 20

// Identity is the local Shadow's signing identity used by Client to mint
// session tokens and Verifiable Presentations.
type Identity struct {
	DID    string
	KeyID  string
	Key    crypto.KeyPair
	Holder *crypto.KeyPair // alias for Key when the holder == identity
}

// PresentationMinter mints a Verifiable Presentation bound to (audience, nonce)
// from the holder's stash of credentials. Implementations live in the agent
// runtime; pkg/a2a does not impose a credential storage model.
type PresentationMinter interface {
	Mint(ctx context.Context, audience, nonce string, iat, exp time.Time) (string, error)
}

// PeerEndpoint is the remote Shadow we're talking to.
type PeerEndpoint struct {
	URL string // base URL, e.g. "https://shadow.example/u/alice"
	DID string
}

// Client is the outbound A2A surface.
type Client struct {
	HTTPClient *http.Client
	Identity   Identity
	Minter     PresentationMinter
	Now        func() time.Time

	// Optional: how long we keep a successful VP cached for a peer.
	VPLifetime time.Duration
}

// NewClient returns a Client with safe defaults.
func NewClient(id Identity, minter PresentationMinter) *Client {
	return &Client{
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS13},
				ResponseHeaderTimeout: 10 * time.Second,
				IdleConnTimeout:       60 * time.Second,
			},
		},
		Identity:   id,
		Minter:     minter,
		VPLifetime: 60 * time.Second,
	}
}

func (c *Client) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now().UTC()
}

// SendMessage performs message:send to peer. It transparently handles the
// 401 presentation_required → re-mint VP → retry flow described in
// RFC-0006 §Re-presentation.
func (c *Client) SendMessage(ctx context.Context, peer PeerEndpoint, msg Message) (*Task, error) {
	return c.callMessage(ctx, peer, MethodMessageSend, msg)
}

// CancelTask performs task:cancel on a peer.
func (c *Client) CancelTask(ctx context.Context, peer PeerEndpoint, taskID string) (*Task, error) {
	return c.call(ctx, peer, MethodTaskCancel, map[string]string{"id": taskID})
}

// GetTask performs task:get on a peer.
func (c *Client) GetTask(ctx context.Context, peer PeerEndpoint, taskID string) (*Task, error) {
	return c.call(ctx, peer, MethodTaskGet, map[string]string{"id": taskID})
}

// callMessage is the message:send / message:stream wrapper. It mints a fresh
// VP on the first attempt; on a 401 presentation_required, it re-mints
// against the supplied nonce and retries once.
func (c *Client) callMessage(ctx context.Context, peer PeerEndpoint, method string, msg Message) (*Task, error) {
	// First attempt: include a VP minted with a client-chosen nonce.
	nonce := newNonce()
	resp, body, err := c.sendOnce(ctx, peer, method, map[string]any{"message": msg}, nonce)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized {
		// Decode the {error, nonce} envelope and retry with the supplied nonce.
		var prc presentationRequiredBody
		if err := json.Unmarshal(body, &prc); err != nil {
			return nil, fmt.Errorf("a2a: 401 body parse: %w", err)
		}
		if prc.Error != CodePresentationRequired || prc.Nonce == "" {
			return nil, fmt.Errorf("a2a: unauthorized: %s", body)
		}
		resp, body, err = c.sendOnce(ctx, peer, method, map[string]any{"message": msg}, prc.Nonce)
		if err != nil {
			return nil, err
		}
	}
	return parseTaskResponse(resp, body)
}

// call is the non-message JSON-RPC wrapper. No 401-retry — task:get and
// task:cancel come after a session is already established.
func (c *Client) call(ctx context.Context, peer PeerEndpoint, method string, params any) (*Task, error) {
	resp, body, err := c.sendOnce(ctx, peer, method, params, "")
	if err != nil {
		return nil, err
	}
	return parseTaskResponse(resp, body)
}

// sendOnce mints a session token and (optionally) a VP, then makes a single
// JSON-RPC POST. Returns (response, body, err). nonce empty means: do not
// attach an X-Shadownet-Presentation header.
func (c *Client) sendOnce(ctx context.Context, peer PeerEndpoint, method string, params any, nonce string) (*http.Response, []byte, error) {
	now := c.now()
	jti := "ses-" + newID()
	tok, err := IssueSessionToken(c.Identity.Key, c.Identity.DID, c.Identity.KeyID, peer.DID, jti, now, now.Add(MaxSessionTokenLifetime))
	if err != nil {
		return nil, nil, fmt.Errorf("a2a: mint session token: %w", err)
	}
	rpcBody, err := json.Marshal(jsonrpcRequest{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`"1"`),
		Method:  method,
		Params:  jsonRaw(params),
	})
	if err != nil {
		return nil, nil, fmt.Errorf("a2a: marshal request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, peer.URL+"/a2a", bytes.NewReader(rpcBody))
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+tok)
	if nonce != "" && c.Minter != nil {
		vp, err := c.Minter.Mint(ctx, peer.DID, nonce, now, now.Add(120*time.Second))
		if err != nil {
			return nil, nil, fmt.Errorf("a2a: mint VP: %w", err)
		}
		req.Header.Set("X-Shadownet-Presentation", vp)
	}
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, nil, fmt.Errorf("a2a: %s %s: %w", method, peer.URL, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(http.MaxBytesReader(nil, resp.Body, MaxClientResponseBytes))
	if err != nil {
		return nil, nil, fmt.Errorf("a2a: read response: %w", err)
	}
	return resp, body, nil
}

func jsonRaw(v any) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

type presentationRequiredBody struct {
	Error string `json:"error"`
	Nonce string `json:"nonce"`
}

func parseTaskResponse(resp *http.Response, body []byte) (*Task, error) {
	// 200 OK = inbox delivery; 202 Accepted = quarantined (RFC-0006 §Sender
	// behavior on quarantine). Both carry a JSON-RPC result body with the
	// task. Other statuses indicate errors.
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		var ferr struct {
			Error  string `json:"error"`
			Detail string `json:"detail"`
		}
		_ = json.Unmarshal(body, &ferr)
		if ferr.Error != "" {
			return nil, &Error{Code: ferr.Error, Detail: ferr.Detail}
		}
		return nil, fmt.Errorf("a2a: status %d: %s", resp.StatusCode, body)
	}
	var rpc jsonrpcResponse
	if err := json.Unmarshal(body, &rpc); err != nil {
		return nil, fmt.Errorf("a2a: parse JSON-RPC response: %w", err)
	}
	if rpc.Error != nil {
		return nil, errors.New("a2a: JSON-RPC error: " + rpc.Error.Message)
	}
	raw, err := json.Marshal(rpc.Result)
	if err != nil {
		return nil, fmt.Errorf("a2a: re-encode result: %w", err)
	}
	var t Task
	if err := json.Unmarshal(raw, &t); err != nil {
		return nil, fmt.Errorf("a2a: decode task: %w", err)
	}
	return &t, nil
}
