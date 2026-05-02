// SPDX-License-Identifier: MIT

package cli

import (
	"fmt"
	"io"
	"os"
)

// Run dispatches argv to a subcommand. argv[0] is the program name.
func Run(args []string, stdout, stderr io.Writer) int {
	if len(args) < 2 || args[1] == "-h" || args[1] == "--help" {
		printUsage(stdout)
		if len(args) >= 2 && (args[1] == "-h" || args[1] == "--help") {
			return 0
		}
		return 2
	}
	cmd := args[1]
	rest := args[2:]
	var err error
	switch cmd {
	case "keygen":
		err = Keygen(rest, stdout, stderr)
	case "resolve":
		err = Resolve(rest, stdout, stderr)
	case "inspect":
		err = Inspect(rest, stdout, stderr)
	case "handshake":
		err = Handshake(rest, stdout, stderr)
	case "doctor":
		err = Doctor(rest, stdout, stderr)
	case "version":
		fmt.Fprintln(stdout, "shadownet (Shadownet Go SDK & CLI)")
		fmt.Fprintln(stdout, "protocol: v0.1")
		return 0
	default:
		fmt.Fprintf(stderr, "shadownet: unknown subcommand %q\n", cmd)
		printUsage(stderr)
		return 2
	}
	if err != nil {
		fmt.Fprintln(stderr, "shadownet: "+err.Error())
		return 1
	}
	return 0
}

func printUsage(w io.Writer) {
	fmt.Fprintln(w, `shadownet — Shadownet protocol CLI

Usage:
  shadownet <command> [flags]

Commands:
  keygen         generate an Ed25519 keypair (did:key)
  resolve        resolve a Shadowname via its SNS provider
  inspect        decode and validate a Shadownet JWT (VC, VP, freshness, SNS record, session token)
  handshake      run an end-to-end A2A handshake against a peer
  doctor         sanity-check local config and remote endpoints
  version        print version info`)
}

// Args reads positional arguments. Errors are written to stderr.
func Args(_ ...string) []string { return os.Args }
