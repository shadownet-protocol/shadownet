// SPDX-License-Identifier: MIT

// Command shadownet is the operator + developer CLI for the Shadownet
// protocol. It bundles keygen, name resolution, JWT inspection, an A2A
// handshake driver, and a deployment doctor.
package main

import (
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/cli"
)

// version is stamped at build time by the release pipeline via
// `-ldflags "-X main.version=$tag"`. Local builds keep the "dev" sentinel.
var version = "dev"

func main() {
	os.Exit(cli.Run(os.Args, os.Stdout, os.Stderr, version))
}
