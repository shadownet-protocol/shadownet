module github.com/shadownet-protocol/shadownet/examples/birthday-credential-go

go 1.25.0

require github.com/shadownet-protocol/shadownet/core v0.2.0

require github.com/go-jose/go-jose/v4 v4.1.4 // indirect

// Resolve the SDK from the in-repo checkout for example builds. Drop this
// replace and bump the `require` line to consume a published release.
replace github.com/shadownet-protocol/shadownet/core => ../../core
