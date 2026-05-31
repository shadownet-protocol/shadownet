// SPDX-License-Identifier: MIT

// Command issuer-server is the Shadownet Issuer HTTP reference server
// (RFC 0001 §6). It supports both domain mode (well-known paths) and
// keyed-Hub mode (self-served AgentCard + configurable paths), backed by
// SQLite. The Postgres-backed variant lives in
// core/pgstore/cmd/issuer-server-pg and reuses the same command surface
// via internal/issuer/cmdrun.
//
// Subcommands:
//
//	issuer-server serve  --config issuer.yaml
//	issuer-server admin approve     --config issuer.yaml --handle <hex>
//	issuer-server admin reject      --config issuer.yaml --handle <hex> [--reason "..."]
//	issuer-server admin revoke      --config issuer.yaml --epoch <n> --idx <n>
//	issuer-server admin rotate-epoch --config issuer.yaml
//	issuer-server admin list-pending --config issuer.yaml [--status new|approved|rejected]
//
// Config file (YAML):
//
//	listen: 127.0.0.1:8444
//	mode: domain                  # or "keyed"
//	issuerIdentifier: acme.example  # domain or z6Mk… pubkey
//	cacheMaxAge: 300              # status-list Cache-Control
//	signing:
//	  keyfile: ./issuer.jwk
//	storage:
//	  driver: sqlite
//	  dsn: ./issuer.db
//	  maxIndicesPerEpoch: 131072
//	tls:
//	  cert: ./tls.crt             # optional; omit for plaintext (loopback only)
//	  key:  ./tls.key
//	hook:
//	  driver: queue               # queue | dev
//	  nextURL: https://acme.example/.well-known/shadownet/issue
//	# Required only when mode == keyed:
//	keyedAgentCard:
//	  name: "Acme Hub"
//	  description: "Membership credentials for acme.example"
//	  a2aURL:         https://hub.acme.example/a2a
//	  issueURL:       https://hub.acme.example/issue
//	  statusListBase: https://hub.acme.example/status
package main

import (
	"context"
	"fmt"
	"os"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/cmdrun"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/sqlitestore"
)

func main() {
	err := cmdrun.Main(os.Args[1:], cmdrun.Options{
		BinaryName:    "issuer-server",
		StorageDriver: "sqlite",
		OpenStore:     openSqliteStore,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "issuer-server:", err)
		os.Exit(1)
	}
}

func openSqliteStore(_ context.Context, dsn string, maxIndices uint64) (issuer.Store, error) {
	return sqlitestore.Open(dsn, maxIndices)
}
