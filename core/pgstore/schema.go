// SPDX-License-Identifier: MIT

package pgstore

import (
	_ "embed"
)

// schemaSQL is applied on Open. It uses CREATE TABLE IF NOT EXISTS so it is
// safe to run on every boot; this is sufficient for v0.1.x where there is
// only one schema version. Future versions add a migrations table.
//
//go:embed schema.sql
var schemaSQL string
