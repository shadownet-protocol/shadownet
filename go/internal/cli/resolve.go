// SPDX-License-Identifier: MIT

package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/go/pkg/sns"
)

// Resolve implements `shadownet resolve <shadowname>`.
func Resolve(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("resolve", flag.ContinueOnError)
	fs.SetOutput(stderr)
	timeout := fs.Duration("timeout", 10*time.Second, "fetch timeout")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: shadownet resolve <shadowname>")
	}
	name := fs.Arg(0)

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	resolver := sns.NewResolver(did.NewResolver(did.NewWebResolver()))
	rec, err := resolver.Resolve(ctx, name)
	if err != nil {
		return err
	}

	body := map[string]any{
		"shadowname":  rec.Subject,
		"did":         rec.Record.DID,
		"endpoint":    rec.Record.Endpoint,
		"publicKey":   rec.Record.PublicKey,
		"subjectType": rec.Record.SubjectType,
		"ttl":         rec.Record.TTL,
		"issuer":      rec.Issuer,
		"expires":     rec.Expires.Format(time.RFC3339),
	}
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(body)
}

// fmtErr is unused but reserved for future structured error formatting.
var _ = fmt.Errorf
