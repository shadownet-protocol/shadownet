// SPDX-License-Identifier: MIT

package cli

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"strings"
)

// Inspect implements `shadownet inspect <jwt>`.
//
// inspect is intentionally signature-permissive — it decodes the unverified
// payload so operators can debug malformed artifacts. For verification,
// use `pkg/crypto.VerifyJWT` directly with a known public key.
func Inspect(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("inspect", flag.ContinueOnError)
	fs.SetOutput(stderr)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: shadownet inspect <jwt>")
	}
	compact := fs.Arg(0)
	parts := strings.Split(compact, ".")
	if len(parts) != 3 {
		return fmt.Errorf("inspect: expected 3 segments, got %d", len(parts))
	}
	hdr, err := decodeJSON(parts[0])
	if err != nil {
		return fmt.Errorf("decode header: %w", err)
	}
	body, err := decodeJSON(parts[1])
	if err != nil {
		return fmt.Errorf("decode payload: %w", err)
	}
	out := map[string]any{
		"header":  hdr,
		"payload": body,
	}
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}

func decodeJSON(seg string) (any, error) {
	raw, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		// Some JWT producers pad; try standard URL encoding.
		raw, err = base64.URLEncoding.DecodeString(seg)
		if err != nil {
			return nil, err
		}
	}
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil, err
	}
	return v, nil
}
