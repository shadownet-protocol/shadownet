# shadownet-x402/CLAUDE.md

Conventions follow the root and `python-sdk/` guides. This package wraps the
`x402-avm` SDK for Algorand settlement — **do not reimplement chain signing or
facilitator settlement**; wrap it behind `facilitator.py` / `client.py` /
`wallet.py` so an upstream API change is contained to one module.

The HTTP identity-gate profile (the `Shadow-Credential` / `Shadow-PoP` headers,
the nonce challenge, the PoP audience binding) lives **here, not in the SDK** —
it is an x402 transport binding, a clearly-scoped profile, not ratified v0.2.

Identity verification is **reused** from `shadownet` (`verify_credential`,
`check_revocation`, `satisfies_policy`, `Ed25519KeyPair`) — never duplicated.

Gate before pushing: `uv run ruff check . && uv run ruff format --check . &&
uv run mypy src/shadownet_x402 && uv run pytest`.
