// SPDX-License-Identifier: MIT

// Package pgstore is the Postgres-backed Store implementations for the
// Shadownet reference SCA and SNS servers.
//
// pgstore is a *separate Go module* from the parent shadownet-go module:
// importing pkg/sca, pkg/sns, or any other parent package does NOT pull
// `github.com/jackc/pgx/v5` into your dependency graph. Operators on
// SQLite-only deployments stay clean of pgx; deployments that want Postgres
// add this submodule explicitly.
//
// The exported types implement the contracts in pkg/sca and pkg/sns; passing
// pkg/sca/storetest and pkg/sns/storetest is the protocol-conformance bar.
//
// Concurrency-correct AssignIndex (atomic UPDATE … RETURNING under
// READ COMMITTED) and idempotent Revoke (INSERT … ON CONFLICT DO NOTHING)
// avoid the silent-revocation-loss race that a fetch-modify-write of a BLOB
// would have on a multi-writer Postgres.
package pgstore
