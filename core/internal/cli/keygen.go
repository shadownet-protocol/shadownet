// SPDX-License-Identifier: MIT

package cli

import (
	"flag"
	"fmt"
	"io"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

// Keygen implements `shadownet keygen`. The v0.2 multibase-encoded public key
// output is added in the Phase 2 substrate; for the Phase 1 cut this command
// emits the raw JWK x field and saves a keyfile with an empty kid (Phase 2
// rewrite re-introduces the multibase `z6Mk...` form and the agentcard-style
// kid). Tracked: see /Users/perfect/.claude-work/plans/resilient-hugging-graham.md.
func Keygen(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("keygen", flag.ContinueOnError)
	fs.SetOutput(stderr)
	out := fs.String("out", "", "path to write the private JWK (mode 0600)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	kp, err := crypto.Generate()
	if err != nil {
		return err
	}
	pub, err := crypto.PublicJWK(kp.Public, "")
	if err != nil {
		return err
	}
	fmt.Fprintln(stdout, pub.X)
	if *out != "" {
		if err := crypto.SaveKeyFile(*out, kp, ""); err != nil {
			return err
		}
		fmt.Fprintln(stdout, "wrote", *out, "(mode 0600)")
	}
	return nil
}
