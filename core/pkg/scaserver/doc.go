// SPDX-License-Identifier: MIT

// Package scaserver is the reusable HTTP-server bootstrap for an SCA built
// atop pkg/sca.
//
// It is what `cmd/sca-server/main.go` (and `pgstore/cmd/sca-server/main.go`)
// reduce to once the per-binary concerns (config loading, store selection,
// proof-method registration) are factored out. Operators wiring a custom
// SCA — different storage backend, different proof method, additional
// middleware — call Run with their already-constructed *sca.Issuer.
//
// Run handles: hardened http.Server defaults, TLS termination, panic
// recovery, request-id propagation, structured access logging, signal-driven
// graceful shutdown.
package scaserver
