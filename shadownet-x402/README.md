# shadownet-x402

Identity-gated [x402](https://github.com/x402-foundation/x402) payments on
Algorand for [Shadownet](https://github.com/shadownet-protocol/shadownet).

A buyer agent pays an x402 resource server, and the payment is bound to a
**verified, revocable Shadow identity** instead of an anonymous wallet. Settles
USDC on Algorand via the hosted GoPlausible facilitator. Identity verification
reuses the `shadownet` SDK; this package adds the payment binding, the HTTP
identity gate, the resource-server paywall, and the buyer client.

Design and roadmap: [`docs/shadowpay.md`](../docs/shadowpay.md).

## Layout

- `src/shadownet_x402/` — the library (gate, paywall, client, settlement).
- `demo/` — a runnable venue server + buyer (Algorand TestNet).

## Develop

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src/shadownet_x402 && uv run pytest
```

The unit suite runs with no network and no chain (the facilitator is stubbed).
TestNet integration tests are marked `network` and skip unless configured.
