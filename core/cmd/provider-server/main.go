// SPDX-License-Identifier: MIT

// Command provider-server is the Shadownet Provider HTTP reference server
// (RFC 0001 §5.2). It hosts signed A2A AgentCards at <ep>/identity/<local>
// for the Shadownames registered in its SQLite store, and exposes admin
// subcommands for record CRUD and DNS TXT generation. The Postgres-backed
// variant lives in core/pgstore/cmd/provider-server-pg and reuses the
// same command surface via internal/provider/cmdrun.
//
// Subcommands:
//
//	provider-server serve --config provider.yaml
//	provider-server dns-record --config provider.yaml [--issuer]
//	provider-server admin add    --config provider.yaml --local alice --pk z6Mk... --a2a-url https://...
//	provider-server admin remove --config provider.yaml --local alice
//	provider-server admin list   --config provider.yaml
//
// Config file (YAML):
//
//	listen: 127.0.0.1:8443
//	domain: sh4dow.org
//	cacheMaxAge: 3600
//	signing:
//	  keyfile: ./provider.jwk
//	storage:
//	  driver: sqlite
//	  dsn: ./provider.db
//	tls:
//	  cert: ./tls.crt        # optional; omit for plaintext (loopback only)
//	  key:  ./tls.key
//	dnsEndpoint: https://shadow.sh4dow.org/v1   # printed by `dns-record`
package main

import (
	"context"
	"fmt"
	"io"
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/provider"
	"github.com/shadownet-protocol/shadownet/core/internal/provider/cmdrun"
	"github.com/shadownet-protocol/shadownet/core/internal/provider/sqlitestore"
)

func main() {
	err := cmdrun.Main(os.Args[1:], cmdrun.Options{
		BinaryName:    "provider-server",
		StorageDriver: "sqlite",
		OpenStore:     openSqliteStore,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "provider-server:", err)
		os.Exit(1)
	}
}

func openSqliteStore(_ context.Context, dsn string) (provider.Store, io.Closer, error) {
	s, err := sqlitestore.Open(dsn)
	if err != nil {
		return nil, nil, err
	}
	return s, s, nil
}
