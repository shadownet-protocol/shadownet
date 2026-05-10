// SPDX-License-Identifier: MIT

package vc

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
)

// StatusList is a bit-indexed revocation list per the W3C BitstringStatusList
// specification (RFC-0003 §Revocation). A bit set to 1 means revoked.
//
// Encoding: the bit array is gzip-compressed and then base64url-encoded
// without padding, exactly the wire form W3C specifies for the
// `encodedList` field of a status list credential.
type StatusList struct {
	bits []byte
	size uint64
}

// NewStatusList builds an empty list large enough to address `size` indices.
// Size is rounded up to a multiple of 8 internally.
func NewStatusList(size uint64) *StatusList {
	if size == 0 {
		size = 8
	}
	n := (size + 7) / 8
	return &StatusList{bits: make([]byte, n), size: size}
}

// Size returns the number of indices the list addresses.
func (s *StatusList) Size() uint64 { return s.size }

// Get returns the bit value at idx.
func (s *StatusList) Get(idx uint64) (bool, error) {
	if idx >= s.size {
		return false, fmt.Errorf("vc: status index %d out of range (size %d)", idx, s.size)
	}
	return s.bits[idx/8]&(1<<(7-idx%8)) != 0, nil
}

// Set writes the bit value at idx.
func (s *StatusList) Set(idx uint64, val bool) error {
	if idx >= s.size {
		return fmt.Errorf("vc: status index %d out of range (size %d)", idx, s.size)
	}
	mask := byte(1 << (7 - idx%8))
	if val {
		s.bits[idx/8] |= mask
	} else {
		s.bits[idx/8] &^= mask
	}
	return nil
}

// Encode returns the gzip+base64url-encoded representation that goes into the
// encodedList field of a status list credential.
func (s *StatusList) Encode() (string, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(s.bits); err != nil {
		return "", fmt.Errorf("vc: gzip status list: %w", err)
	}
	if err := gz.Close(); err != nil {
		return "", fmt.Errorf("vc: gzip close: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf.Bytes()), nil
}

// DecodeStatusList parses an encodedList value into a StatusList. The size in
// bits is inferred from the decompressed body length (×8).
func DecodeStatusList(encoded string) (*StatusList, error) {
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("vc: decode status list base64: %w", err)
	}
	gr, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, fmt.Errorf("vc: gunzip: %w", err)
	}
	defer func() { _ = gr.Close() }()
	bits, err := io.ReadAll(gr)
	if err != nil {
		return nil, fmt.Errorf("vc: read gunzip: %w", err)
	}
	if len(bits) == 0 {
		return nil, errors.New("vc: empty status list")
	}
	return &StatusList{bits: bits, size: uint64(len(bits)) * 8}, nil
}

// StatusFetcher resolves a credentialStatus.statusListCredential URL to a
// usable StatusList. Implementations typically fetch + verify a status-list
// VC; HTTPStatusFetcher below is the v0.1 reference implementation.
type StatusFetcher interface {
	Fetch(ctx context.Context, statusListCredential string) (*StatusList, error)
}

// StatusPurposeRevocation is the value used for revocation lists per the
// W3C BitstringStatusList spec.
const StatusPurposeRevocation = "revocation"

// BitstringStatusListCredentialType is the VC type discriminator.
const BitstringStatusListCredentialType = "BitstringStatusListCredential"

// StatusListPublication describes a status-list credential the issuer is
// about to sign or has just verified.
type StatusListPublication struct {
	ID            string // the URL at which this list is served (also vc.id)
	Issuer        string // SCA DID
	StatusPurpose string // typically StatusPurposeRevocation
	EncodedList   string // already-encoded (gzip+base64url) bitstring
	IssuedAt      time.Time
	Expires       time.Time
}

// IssueStatusListCredential signs a status-list VC as a JWT (typ: vc+jwt).
// kid is the issuer's DID URL with key fragment.
func IssueStatusListCredential(kp crypto.KeyPair, p StatusListPublication, kid string) (string, error) {
	if p.ID == "" || p.Issuer == "" || p.EncodedList == "" {
		return "", errors.New("vc: status list publication requires ID, Issuer, EncodedList")
	}
	if p.StatusPurpose == "" {
		p.StatusPurpose = StatusPurposeRevocation
	}
	if p.IssuedAt.IsZero() {
		return "", errors.New("vc: status list IssuedAt required")
	}
	if p.Expires.IsZero() {
		// W3C does not require exp; we apply a short lifetime to fit the
		// max-age=300 cache hint from RFC-0003 §Revocation.
		p.Expires = p.IssuedAt.Add(5 * time.Minute)
	}
	if !p.Expires.After(p.IssuedAt) {
		return "", errors.New("vc: status list Expires must be after IssuedAt")
	}
	issDID, _ := did.SplitDIDURL(kid)
	if issDID != p.Issuer {
		return "", fmt.Errorf("vc: kid DID %q does not match issuer %q", issDID, p.Issuer)
	}
	claims := wireStatusListVC{
		Iss:     p.Issuer,
		Sub:     p.ID,
		Iat:     p.IssuedAt.Unix(),
		Exp:     p.Expires.Unix(),
		Version: Version,
		VC: wireStatusListBody{
			Context: []string{ContextW3CCredentialsV2},
			ID:      p.ID,
			Type:    []string{CredentialType, BitstringStatusListCredentialType},
			CredentialSubject: wireStatusListSubject{
				ID:            p.ID + "#list",
				Type:          "BitstringStatusList",
				StatusPurpose: p.StatusPurpose,
				EncodedList:   p.EncodedList,
			},
		},
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{KeyID: kid, Type: TypVCJWT})
}

// VerifyStatusListCredential parses a status-list JWT, verifies its signature
// against the issuer's DID document, asserts shape, and returns both the
// decoded StatusList and the publication metadata.
func VerifyStatusListCredential(ctx context.Context, r did.Resolver, compact string, now time.Time) (*StatusList, *StatusListPublication, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, nil, err
	}
	if hdr.Typ != TypVCJWT {
		return nil, nil, fmt.Errorf("vc: status list typ = %q, want %q", hdr.Typ, TypVCJWT)
	}
	if hdr.Kid == "" {
		return nil, nil, errors.New("vc: status list missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, nil, fmt.Errorf("vc: resolve status list issuer key: %w", err)
	}
	var w wireStatusListVC
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, nil, err
	}
	if w.Version != Version {
		return nil, nil, fmt.Errorf("vc: status list shadownet:v = %q, want %q", w.Version, Version)
	}
	if !now.IsZero() && w.Exp != 0 && now.Unix() >= w.Exp {
		return nil, nil, errors.New("vc: status list expired")
	}
	if !containsString(w.VC.Type, BitstringStatusListCredentialType) {
		return nil, nil, fmt.Errorf("vc: status list type missing %q", BitstringStatusListCredentialType)
	}
	if w.VC.CredentialSubject.Type != "BitstringStatusList" {
		return nil, nil, fmt.Errorf("vc: status list subject type = %q", w.VC.CredentialSubject.Type)
	}
	list, err := DecodeStatusList(w.VC.CredentialSubject.EncodedList)
	if err != nil {
		return nil, nil, err
	}
	publ := &StatusListPublication{
		ID:            w.VC.ID,
		Issuer:        w.Iss,
		StatusPurpose: w.VC.CredentialSubject.StatusPurpose,
		EncodedList:   w.VC.CredentialSubject.EncodedList,
		IssuedAt:      time.Unix(w.Iat, 0).UTC(),
		Expires:       time.Unix(w.Exp, 0).UTC(),
	}
	return list, publ, nil
}

// HTTPStatusFetcher implements StatusFetcher by GETting the URL named in a
// credential's credentialStatus.statusListCredential, then verifying the
// returned status-list VC against the issuer's DID document.
type HTTPStatusFetcher struct {
	Client   *http.Client
	Resolver did.Resolver
	Now      func() time.Time
}

// NewHTTPStatusFetcher builds a fetcher with safe defaults: TLS 1.3 and a
// 10-second timeout. Caller MUST set Resolver.
func NewHTTPStatusFetcher(r did.Resolver) *HTTPStatusFetcher {
	return &HTTPStatusFetcher{
		Client: &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS13},
				ResponseHeaderTimeout: 5 * time.Second,
				IdleConnTimeout:       30 * time.Second,
			},
		},
		Resolver: r,
		Now:      time.Now,
	}
}

// Fetch implements StatusFetcher.
func (f *HTTPStatusFetcher) Fetch(ctx context.Context, url string) (*StatusList, error) {
	if f.Resolver == nil {
		return nil, errors.New("vc: HTTPStatusFetcher.Resolver required")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("vc: build status list request: %w", err)
	}
	req.Header.Set("Accept", "application/jwt, application/vc+jwt")
	resp, err := f.Client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("vc: fetch status list: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("vc: status list %s: status %d", url, resp.StatusCode)
	}
	body, err := io.ReadAll(http.MaxBytesReader(nil, resp.Body, 1<<20))
	if err != nil {
		return nil, fmt.Errorf("vc: read status list: %w", err)
	}
	now := f.Now
	if now == nil {
		now = time.Now
	}
	list, _, err := VerifyStatusListCredential(ctx, f.Resolver, string(body), now())
	return list, err
}

type wireStatusListVC struct {
	Iss     string             `json:"iss"`
	Sub     string             `json:"sub,omitempty"`
	Iat     int64              `json:"iat"`
	Exp     int64              `json:"exp,omitempty"`
	Version string             `json:"shadownet:v"`
	VC      wireStatusListBody `json:"vc"`
}

type wireStatusListBody struct {
	Context           []string              `json:"@context"`
	ID                string                `json:"id"`
	Type              []string              `json:"type"`
	CredentialSubject wireStatusListSubject `json:"credentialSubject"`
}

type wireStatusListSubject struct {
	ID            string `json:"id"`
	Type          string `json:"type"`
	StatusPurpose string `json:"statusPurpose"`
	EncodedList   string `json:"encodedList"`
}
