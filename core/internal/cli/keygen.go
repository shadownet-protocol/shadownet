// SPDX-License-Identifier: MIT

package cli

import (
	"flag"
	"fmt"
	"io"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
)

// Keygen implements `shadownet keygen`.
func Keygen(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("keygen", flag.ContinueOnError)
	fs.SetOutput(stderr)
	out := fs.String("out", "", "path to write the private JWK (mode 0600); if empty, only print the DID")
	if err := fs.Parse(args); err != nil {
		return err
	}
	kp, err := crypto.Generate()
	if err != nil {
		return err
	}
	d, err := did.EncodeKey(kp.Public)
	if err != nil {
		return err
	}
	fmt.Fprintln(stdout, d)
	if *out != "" {
		if err := crypto.SaveKeyFile(*out, kp, d+"#"+d[len("did:key:"):]); err != nil {
			return err
		}
		fmt.Fprintln(stdout, "wrote", *out, "(mode 0600)")
	}
	return nil
}
