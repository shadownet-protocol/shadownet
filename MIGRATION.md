# Migration guide

This document explains what changed for consumers of the Shadownet Go and
Python SDKs after the consolidation of `shadownet-protocol/shadownet-go` and
`shadownet-protocol/shadownet-py` into the
[`shadownet-protocol/shadownet`](https://github.com/shadownet-protocol/shadownet)
monorepo.

If you don't depend on either SDK, you can stop reading.

## What changed at the repo level

The two previously-standalone repositories have been merged into one. Each SDK
now lives in its own subtree:

| Old repo | New location |
| --- | --- |
| `shadownet-protocol/shadownet-go` | `shadownet-protocol/shadownet` → `go/` |
| `shadownet-protocol/shadownet-py` | `shadownet-protocol/shadownet` → `py/` |

Full commit history from both source repositories has been preserved
(`git log -- go/` and `git log -- py/` will show the per-language
history, with original authors, dates, and messages). The old repositories
remain readable but no longer accept new commits.

## Go SDK

### Module path — BREAKING

| Before | After |
| --- | --- |
| `github.com/shadownet-protocol/shadownet-go` | `github.com/shadownet-protocol/shadownet/go` |
| `github.com/shadownet-protocol/shadownet-go/pgstore` | `github.com/shadownet-protocol/shadownet/go/pgstore` |

To migrate, in your project:

```sh
# Drop the old require/replace, add the new module:
go mod edit -droprequire github.com/shadownet-protocol/shadownet-go || true
go mod edit -dropreplace github.com/shadownet-protocol/shadownet-go || true
go get github.com/shadownet-protocol/shadownet/go@v0.2.0

# Mass-rewrite imports across the codebase:
find . -type f -name '*.go' -exec sed -i.bak \
  -e 's|github.com/shadownet-protocol/shadownet-go|github.com/shadownet-protocol/shadownet/go|g' {} +
find . -name '*.bak' -delete
go mod tidy
```

The `v0.1.x` releases of the old module path remain on the Go module proxy.
Existing consumers pinned to `v0.1.7` (or any earlier `v0.1.x`) continue to
work without changes; the migration is required only when you want to pick up
`v0.2.0` or later.

### CLI install

```sh
# Before:
go install github.com/shadownet-protocol/shadownet-go/cmd/shadownet@latest

# After:
go install github.com/shadownet-protocol/shadownet/go/cmd/shadownet@latest
```

### Container images

No change. The four reference images continue to publish at the same paths:

- `ghcr.io/shadownet-protocol/sca-server:<tag>`
- `ghcr.io/shadownet-protocol/sns-server:<tag>`
- `ghcr.io/shadownet-protocol/sca-server-pg:<tag>`
- `ghcr.io/shadownet-protocol/sns-server-pg:<tag>`

Tags reflect the new `go/vX.Y.Z` scheme starting at `v0.2.0`.

### Tag scheme

Within the monorepo, tags carry the directory prefix Go requires for
sub-module subtrees:

| Module | Tag pattern |
| --- | --- |
| Main module (`go/`) | `go/vX.Y.Z` |
| pgstore submodule | `go/pgstore/vX.Y.Z` |

Old `v0.1.x` and `pgstore/v0.1.x` tags remain on the legacy repository.

## Python SDK

### Imports & PyPI — no change required

The PyPI distribution (`shadownet`) and every importable name (`shadownet.*`)
are unchanged. The minimum supported Python version (`>= 3.12`) is unchanged.
Existing code continues to work without modification:

```python
# Still works in v0.2.0:
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.vc.credential import issue_credential, verify_credential
```

```sh
# Still works in v0.2.0:
pip install shadownet
uv add shadownet
```

### Package metadata

Repository URLs in package metadata now point at the monorepo:

| Field | Before | After |
| --- | --- | --- |
| `Homepage` | `…/shadownet-py` | `…/shadownet/tree/main/py` |
| `Issues` | `…/shadownet-py/issues` | `…/shadownet/issues` |
| `Repository` (new) | — | `…/shadownet` |
| `Changelog` (new) | — | `…/shadownet/blob/main/py/CHANGELOG.md` |

Update any internal dashboards or CI scripts that cross-reference these URLs.

### Tag scheme

| Releases | Before | After |
| --- | --- | --- |
| Git tag | `v0.1.3` | `py/v0.2.0` |
| PyPI version | `0.1.3` | `0.2.0` |

The `0.1.x` releases remain on PyPI and are unaffected.

### Why `0.2.0` if there's no API change?

The version bump is for parity with the Go SDK's breaking import-path change
and to mark the migration cleanly. There is no Python API change between
`0.1.3` and `0.2.0`.

## Where to file issues now

Use [`shadownet-protocol/shadownet/issues`](https://github.com/shadownet-protocol/shadownet/issues).
The legacy repos' issue trackers are no longer monitored. Any open issues at
the time of the move have been listed in the move-PR description on each
legacy repo.
