// SPDX-License-Identifier: MIT

// Package pgstore is the Postgres-backed Store implementations for the
// Shadownet reference Provider and Issuer servers (RFC 0001 §5.2, §6).
//
// pgstore is a *separate Go module* from the parent core module:
// importing internal/provider, internal/issuer, or any other parent
// package does NOT pull github.com/jackc/pgx/v5 into your dependency
// graph. Operators on SQLite-only deployments stay clean of pgx;
// deployments that want Postgres add this submodule explicitly via the
// provider-server-pg / issuer-server-pg cmd binaries.
//
// The exported types satisfy internal/provider.Store and
// internal/issuer.Store at compile time; the same Go interface contract
// the SQLite implementation satisfies is the protocol-conformance bar.
//
// Concurrency-correct AllocateIndex (SELECT ... FOR UPDATE inside a
// pgx.BeginFunc transaction) and idempotent SetRevoked (INSERT ... ON
// CONFLICT DO NOTHING) avoid the silent-revocation-loss race that a
// fetch-modify-write of a BLOB would have on a multi-writer Postgres.
package pgstore
