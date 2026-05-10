// SPDX-License-Identifier: MIT

// Command go-emit is the Go side of the cross-checked fixture pipeline.
//
// Invocation: `go-emit <kind>` reads a JSON spec from stdin and writes the
// canonical fixture bytes to stdout. The output is byte-compared against the
// Python emitter (`python -m shadownet_conformance.regen.py_emit`) by the
// regen CLI; mismatches block fixture writes.
//
// Both emitters use their SDK's natural signing primitives. The cross-check
// is the wire-level interop guarantee.
package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
)

const (
	contextW3CCredV2  = "https://www.w3.org/ns/credentials/v2"
	contextShadownetV1 = "https://sh4dow.org/contexts/v1"

	typeVC               = "VerifiableCredential"
	typeSubjectCred      = "ShadownetSubjectCredential"
	typeVP               = "VerifiablePresentation"
	typeStatusListCred   = "BitstringStatusListCredential"
	typeStatusList       = "BitstringStatusList"
	typeStatusListEntry  = "BitstringStatusListEntry"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: go-emit <kind>")
		os.Exit(2)
	}
	kind := os.Args[1]

	specBytes, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintf(os.Stderr, "go-emit: read stdin: %v\n", err)
		os.Exit(2)
	}
	var spec map[string]any
	if err := json.Unmarshal(specBytes, &spec); err != nil {
		fmt.Fprintf(os.Stderr, "go-emit: parse spec json: %v\n", err)
		os.Exit(2)
	}

	out, err := dispatch(kind, spec)
	if err != nil {
		fmt.Fprintf(os.Stderr, "go-emit: emit %s: %v\n", kind, err)
		os.Exit(1)
	}
	if _, err := os.Stdout.Write(out); err != nil {
		fmt.Fprintf(os.Stderr, "go-emit: write stdout: %v\n", err)
		os.Exit(2)
	}
}

func dispatch(kind string, spec map[string]any) ([]byte, error) {
	switch kind {
	case "key":
		return emitKey(spec)
	case "credential":
		return emitCredential(spec)
	case "freshness":
		return emitFreshness(spec)
	case "presentation":
		return emitPresentation(spec)
	case "sns_record":
		return emitSNSRecord(spec)
	case "status_list":
		return emitStatusList(spec)
	default:
		return nil, fmt.Errorf("unknown kind %q", kind)
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func keyFor(seedHex string) (ed25519.PrivateKey, ed25519.PublicKey, error) {
	seed, err := hex.DecodeString(seedHex)
	if err != nil {
		return nil, nil, fmt.Errorf("seed hex: %w", err)
	}
	if len(seed) != ed25519.SeedSize {
		return nil, nil, fmt.Errorf("seed length = %d, want %d", len(seed), ed25519.SeedSize)
	}
	priv := ed25519.NewKeyFromSeed(seed)
	pub := priv.Public().(ed25519.PublicKey)
	return priv, pub, nil
}

func didKeyFor(pub ed25519.PublicKey) (string, error) {
	return did.EncodeKey(pub)
}

func mustString(spec map[string]any, k string) (string, error) {
	v, ok := spec[k]
	if !ok {
		return "", fmt.Errorf("missing field %q", k)
	}
	s, ok := v.(string)
	if !ok {
		return "", fmt.Errorf("field %q is not a string", k)
	}
	return s, nil
}

func mustInt64(spec map[string]any, k string) (int64, error) {
	v, ok := spec[k]
	if !ok {
		return 0, fmt.Errorf("missing field %q", k)
	}
	switch n := v.(type) {
	case float64:
		return int64(n), nil
	case int64:
		return n, nil
	case int:
		return int64(n), nil
	default:
		return 0, fmt.Errorf("field %q is not numeric (got %T)", k, v)
	}
}

// publicJWK reproduces shadownet-py's public_jwk() shape exactly, in the same
// key order: kty, crv, x.
type publicJWK struct {
	Kty string `json:"kty"`
	Crv string `json:"crv"`
	X   string `json:"x"`
}

func makePublicJWK(pub ed25519.PublicKey) publicJWK {
	return publicJWK{
		Kty: "OKP",
		Crv: "Ed25519",
		X:   base64.RawURLEncoding.EncodeToString(pub),
	}
}

type privateJWK struct {
	Kty string `json:"kty"`
	Crv string `json:"crv"`
	X   string `json:"x"`
	D   string `json:"d"`
}

func makePrivateJWK(priv ed25519.PrivateKey) privateJWK {
	pub := priv.Public().(ed25519.PublicKey)
	seed := priv.Seed()
	return privateJWK{
		Kty: "OKP",
		Crv: "Ed25519",
		X:   base64.RawURLEncoding.EncodeToString(pub),
		D:   base64.RawURLEncoding.EncodeToString(seed),
	}
}

// ---------------------------------------------------------------------------
// Emitters
// ---------------------------------------------------------------------------

func emitKey(spec map[string]any) ([]byte, error) {
	seedHex, err := mustString(spec, "seed_hex")
	if err != nil {
		return nil, err
	}
	priv, pub, err := keyFor(seedHex)
	if err != nil {
		return nil, err
	}
	didStr, err := didKeyFor(pub)
	if err != nil {
		return nil, err
	}
	pubJWK := makePublicJWK(pub)
	privJWK := makePrivateJWK(priv)
	payload := map[string]any{
		"did":         didStr,
		"public_jwk":  pubJWK,
		"private_jwk": privJWK,
		"seed_hex":    seedHex,
	}
	return canonicalJSONBytes(payload)
}

// vcWire mirrors pkg/vc/credential.go's wire shape EXACTLY (same fields, same
// json tags, same order). We declare it locally rather than importing the
// internal wire type because it is unexported.
type vcWire struct {
	Iss     string    `json:"iss"`
	Sub     string    `json:"sub"`
	Iat     int64     `json:"iat"`
	Exp     int64     `json:"exp"`
	Jti     string    `json:"jti"`
	Version string    `json:"shadownet:v"`
	VC      vcBody    `json:"vc"`
}

type vcBody struct {
	Context           []string  `json:"@context"`
	Type              []string  `json:"type"`
	CredentialSubject vcSubject `json:"credentialSubject"`
	CredentialStatus  *vcStatus `json:"credentialStatus,omitempty"`
}

type vcSubject struct {
	ID          string `json:"id"`
	Level       string `json:"level"`
	SubjectType string `json:"subjectType"`
}

type vcStatus struct {
	Type                 string `json:"type"`
	StatusListIndex      string `json:"statusListIndex"`
	StatusListCredential string `json:"statusListCredential"`
}

func emitCredential(spec map[string]any) ([]byte, error) {
	issuer, err := mustString(spec, "issuer")
	if err != nil {
		return nil, err
	}
	issuerKid, err := mustString(spec, "issuer_kid")
	if err != nil {
		return nil, err
	}
	issuerSeedHex, err := mustString(spec, "issuer_seed_hex")
	if err != nil {
		return nil, err
	}
	subject, err := mustString(spec, "subject")
	if err != nil {
		return nil, err
	}
	level, err := mustString(spec, "level")
	if err != nil {
		return nil, err
	}
	subjectType, err := mustString(spec, "subject_type")
	if err != nil {
		return nil, err
	}
	iat, err := mustInt64(spec, "iat")
	if err != nil {
		return nil, err
	}
	exp, err := mustInt64(spec, "exp")
	if err != nil {
		return nil, err
	}
	jti, err := mustString(spec, "jti")
	if err != nil {
		return nil, err
	}

	wire := vcWire{
		Iss:     issuer,
		Sub:     subject,
		Iat:     iat,
		Exp:     exp,
		Jti:     jti,
		Version: "0.1",
		VC: vcBody{
			Context: []string{contextW3CCredV2, contextShadownetV1},
			Type:    []string{typeVC, typeSubjectCred},
			CredentialSubject: vcSubject{
				ID:          subject,
				Level:       level,
				SubjectType: subjectType,
			},
		},
	}
	if statusRaw, ok := spec["status"]; ok && statusRaw != nil {
		statusMap, ok := statusRaw.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("field \"status\" is not an object")
		}
		idx, err := mustString(statusMap, "status_list_index")
		if err != nil {
			return nil, err
		}
		url, err := mustString(statusMap, "status_list_credential")
		if err != nil {
			return nil, err
		}
		wire.VC.CredentialStatus = &vcStatus{
			Type:                 typeStatusListEntry,
			StatusListIndex:      idx,
			StatusListCredential: url,
		}
	}

	priv, _, err := keyFor(issuerSeedHex)
	if err != nil {
		return nil, err
	}
	tok, err := crypto.SignJWT(priv, wire, crypto.SignerOptions{KeyID: issuerKid, Type: "vc+jwt"})
	if err != nil {
		return nil, err
	}
	return []byte(tok), nil
}

type freshnessWire struct {
	Iss       string `json:"iss"`
	Sub       string `json:"sub"`
	Iat       int64  `json:"iat"`
	Exp       int64  `json:"exp"`
	Freshness string `json:"shadownet:freshness"`
}

func emitFreshness(spec map[string]any) ([]byte, error) {
	issuer, err := mustString(spec, "issuer")
	if err != nil {
		return nil, err
	}
	issuerKid, err := mustString(spec, "issuer_kid")
	if err != nil {
		return nil, err
	}
	issuerSeedHex, err := mustString(spec, "issuer_seed_hex")
	if err != nil {
		return nil, err
	}
	credJti, err := mustString(spec, "credential_jti")
	if err != nil {
		return nil, err
	}
	iat, err := mustInt64(spec, "iat")
	if err != nil {
		return nil, err
	}
	exp, err := mustInt64(spec, "exp")
	if err != nil {
		return nil, err
	}
	wire := freshnessWire{
		Iss:       issuer,
		Sub:       credJti,
		Iat:       iat,
		Exp:       exp,
		Freshness: "v1",
	}
	priv, _, err := keyFor(issuerSeedHex)
	if err != nil {
		return nil, err
	}
	tok, err := crypto.SignJWT(priv, wire, crypto.SignerOptions{KeyID: issuerKid, Type: "JWT"})
	if err != nil {
		return nil, err
	}
	return []byte(tok), nil
}

type vpWire struct {
	Iss   string `json:"iss"`
	Aud   string `json:"aud"`
	Iat   int64  `json:"iat"`
	Exp   int64  `json:"exp"`
	Nonce string `json:"nonce"`
	VP    vpBody `json:"vp"`
}

type vpBody struct {
	Context              []string `json:"@context"`
	Type                 []string `json:"type"`
	VerifiableCredential []string `json:"verifiableCredential"`
}

func emitPresentation(spec map[string]any) ([]byte, error) {
	holderSeedHex, err := mustString(spec, "holder_seed_hex")
	if err != nil {
		return nil, err
	}
	holderDID, err := mustString(spec, "holder_did")
	if err != nil {
		return nil, err
	}
	audDID, err := mustString(spec, "audience_did")
	if err != nil {
		return nil, err
	}
	nonce, err := mustString(spec, "nonce")
	if err != nil {
		return nil, err
	}
	iat, err := mustInt64(spec, "iat")
	if err != nil {
		return nil, err
	}
	exp, err := mustInt64(spec, "exp")
	if err != nil {
		return nil, err
	}
	credsRaw, ok := spec["credentials"]
	if !ok {
		return nil, fmt.Errorf("missing field \"credentials\"")
	}
	credsList, ok := credsRaw.([]any)
	if !ok {
		return nil, fmt.Errorf("field \"credentials\" is not an array")
	}
	creds := make([]string, 0, len(credsList))
	for i, c := range credsList {
		s, ok := c.(string)
		if !ok {
			return nil, fmt.Errorf("credentials[%d] is not a string", i)
		}
		creds = append(creds, s)
	}

	wire := vpWire{
		Iss:   holderDID,
		Aud:   audDID,
		Iat:   iat,
		Exp:   exp,
		Nonce: nonce,
		VP: vpBody{
			Context:              []string{contextW3CCredV2},
			Type:                 []string{typeVP},
			VerifiableCredential: creds,
		},
	}
	priv, _, err := keyFor(holderSeedHex)
	if err != nil {
		return nil, err
	}
	tok, err := crypto.SignJWT(priv, wire, crypto.SignerOptions{
		KeyID: fmt.Sprintf("%s#key-1", holderDID),
		Type:  "vp+jwt",
	})
	if err != nil {
		return nil, err
	}
	return []byte(tok), nil
}

type snsRecordInner struct {
	Shadowname  string    `json:"shadowname"`
	DID         string    `json:"did"`
	Endpoint    string    `json:"endpoint"`
	PublicKey   publicJWK `json:"publicKey"`
	SubjectType string    `json:"subjectType"`
	TTL         int64     `json:"ttl"`
	IssuedAt    int64     `json:"issuedAt"`
	Version     string    `json:"shadownet:v"`
}

type snsRecordWire struct {
	Iss     string         `json:"iss"`
	Sub     string         `json:"sub"`
	Iat     int64          `json:"iat"`
	Exp     int64          `json:"exp"`
	Version string         `json:"shadownet:v"`
	Record  snsRecordInner `json:"record"`
}

func emitSNSRecord(spec map[string]any) ([]byte, error) {
	subjectSeedHex, err := mustString(spec, "subject_seed_hex")
	if err != nil {
		return nil, err
	}
	subjectDID, err := mustString(spec, "subject_did")
	if err != nil {
		return nil, err
	}
	providerDID, err := mustString(spec, "provider_did")
	if err != nil {
		return nil, err
	}
	providerKid, err := mustString(spec, "provider_kid")
	if err != nil {
		return nil, err
	}
	providerSeedHex, err := mustString(spec, "provider_seed_hex")
	if err != nil {
		return nil, err
	}
	shadowname, err := mustString(spec, "shadowname")
	if err != nil {
		return nil, err
	}
	endpoint, err := mustString(spec, "endpoint")
	if err != nil {
		return nil, err
	}
	subjectType, err := mustString(spec, "subject_type")
	if err != nil {
		return nil, err
	}
	ttl, err := mustInt64(spec, "ttl")
	if err != nil {
		return nil, err
	}
	iat, err := mustInt64(spec, "iat")
	if err != nil {
		return nil, err
	}

	_, subjectPub, err := keyFor(subjectSeedHex)
	if err != nil {
		return nil, err
	}

	wire := snsRecordWire{
		Iss:     providerDID,
		Sub:     shadowname,
		Iat:     iat,
		Exp:     iat + ttl,
		Version: "0.1",
		Record: snsRecordInner{
			Shadowname:  shadowname,
			DID:         subjectDID,
			Endpoint:    endpoint,
			PublicKey:   makePublicJWK(subjectPub),
			SubjectType: subjectType,
			TTL:         ttl,
			IssuedAt:    iat,
			Version:     "0.1",
		},
	}
	priv, _, err := keyFor(providerSeedHex)
	if err != nil {
		return nil, err
	}
	tok, err := crypto.SignJWT(priv, wire, crypto.SignerOptions{KeyID: providerKid, Type: "JWT"})
	if err != nil {
		return nil, err
	}
	return []byte(tok), nil
}

type statusListSubject struct {
	ID            string `json:"id"`
	Type          string `json:"type"`
	StatusPurpose string `json:"statusPurpose"`
	EncodedList   string `json:"encodedList"`
}

type statusListBody struct {
	Context           []string          `json:"@context"`
	Type              []string          `json:"type"`
	CredentialSubject statusListSubject `json:"credentialSubject"`
}

type statusListWire struct {
	Iss     string         `json:"iss"`
	Sub     string         `json:"sub"`
	Iat     int64          `json:"iat"`
	Exp     int64          `json:"exp"`
	Version string         `json:"shadownet:v"`
	VC      statusListBody `json:"vc"`
}

func emitStatusList(spec map[string]any) ([]byte, error) {
	issuer, err := mustString(spec, "issuer")
	if err != nil {
		return nil, err
	}
	issuerKid, err := mustString(spec, "issuer_kid")
	if err != nil {
		return nil, err
	}
	issuerSeedHex, err := mustString(spec, "issuer_seed_hex")
	if err != nil {
		return nil, err
	}
	statusListURL, err := mustString(spec, "status_list_url")
	if err != nil {
		return nil, err
	}
	purpose, err := mustString(spec, "purpose")
	if err != nil {
		return nil, err
	}
	iat, err := mustInt64(spec, "iat")
	if err != nil {
		return nil, err
	}
	exp, err := mustInt64(spec, "exp")
	if err != nil {
		return nil, err
	}
	// encoded_list is pre-computed by the regen CLI; gzip output is
	// implementation-defined so we do not encode here.
	encoded, err := mustString(spec, "encoded_list")
	if err != nil {
		return nil, err
	}

	wire := statusListWire{
		Iss:     issuer,
		Sub:     statusListURL,
		Iat:     iat,
		Exp:     exp,
		Version: "0.1",
		VC: statusListBody{
			Context: []string{contextW3CCredV2},
			Type:    []string{typeVC, typeStatusListCred},
			CredentialSubject: statusListSubject{
				ID:            statusListURL + "#list",
				Type:          typeStatusList,
				StatusPurpose: purpose,
				EncodedList:   encoded,
			},
		},
	}
	priv, _, err := keyFor(issuerSeedHex)
	if err != nil {
		return nil, err
	}
	tok, err := crypto.SignJWT(priv, wire, crypto.SignerOptions{KeyID: issuerKid, Type: "vc+jwt"})
	if err != nil {
		return nil, err
	}
	return []byte(tok), nil
}

// ---------------------------------------------------------------------------
// Canonical JSON: sorted keys, 2-space indent, trailing newline.
// Mirrors `json.dumps(payload, sort_keys=True, indent=2) + "\n"` exactly.
// ---------------------------------------------------------------------------

func canonicalJSONBytes(payload any) ([]byte, error) {
	// Round-trip through map[string]any to strip struct field ordering and let
	// the serializer sort keys alphabetically.
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	var m any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	if err := encodeCanonical(&buf, m, 0); err != nil {
		return nil, err
	}
	buf.WriteByte('\n')
	return buf.Bytes(), nil
}

func encodeCanonical(buf *bytes.Buffer, v any, depth int) error {
	switch x := v.(type) {
	case map[string]any:
		if len(x) == 0 {
			buf.WriteString("{}")
			return nil
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sortStrings(keys)
		buf.WriteString("{\n")
		for i, k := range keys {
			writeIndent(buf, depth+1)
			ek, err := json.Marshal(k)
			if err != nil {
				return err
			}
			buf.Write(ek)
			buf.WriteString(": ")
			if err := encodeCanonical(buf, x[k], depth+1); err != nil {
				return err
			}
			if i < len(keys)-1 {
				buf.WriteByte(',')
			}
			buf.WriteByte('\n')
		}
		writeIndent(buf, depth)
		buf.WriteByte('}')
	case []any:
		if len(x) == 0 {
			buf.WriteString("[]")
			return nil
		}
		buf.WriteString("[\n")
		for i, item := range x {
			writeIndent(buf, depth+1)
			if err := encodeCanonical(buf, item, depth+1); err != nil {
				return err
			}
			if i < len(x)-1 {
				buf.WriteByte(',')
			}
			buf.WriteByte('\n')
		}
		writeIndent(buf, depth)
		buf.WriteByte(']')
	default:
		ev, err := json.Marshal(v)
		if err != nil {
			return err
		}
		buf.Write(ev)
	}
	return nil
}

func sortStrings(s []string) {
	// Insertion sort; keys-per-object are small, this avoids importing sort
	// into a hot-path emitter binary.
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j-1] > s[j]; j-- {
			s[j-1], s[j] = s[j], s[j-1]
		}
	}
}

func writeIndent(buf *bytes.Buffer, depth int) {
	for i := 0; i < depth*2; i++ {
		buf.WriteByte(' ')
	}
}

