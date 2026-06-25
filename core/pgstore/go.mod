module github.com/shadownet-protocol/shadownet/core/pgstore

go 1.25.0

require (
	github.com/jackc/pgx/v5 v5.10.0
	github.com/shadownet-protocol/shadownet/core v0.2.1
)

require (
	github.com/go-jose/go-jose/v4 v4.1.4 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	github.com/kr/text v0.2.0 // indirect
	github.com/rogpeppe/go-internal v1.14.1 // indirect
	golang.org/x/sync v0.17.0 // indirect
	golang.org/x/text v0.29.0 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

// Resolve the parent module from the local checkout for in-repo builds (CI,
// dev, and `go test ./...` from this directory). Consumers ignore replace
// directives in required modules and resolve the version pinned in `require`
// above from the proxy.
replace github.com/shadownet-protocol/shadownet/core => ../
