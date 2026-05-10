module github.com/shadownet-protocol/shadownet/conformance/fixtures/_regen/go-emit

go 1.25.0

require github.com/shadownet-protocol/shadownet/core v0.2.0

require github.com/go-jose/go-jose/v4 v4.1.4 // indirect

// Resolve the SDK from the in-repo checkout. The regen CLI builds this
// binary on demand and shells out to it; it is never published as a Go
// module to the proxy.
replace github.com/shadownet-protocol/shadownet/core => ../../../../core
