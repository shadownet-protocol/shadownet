import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// `test.alias` is scoped to the vitest run only — the tsup build keeps
// `openclaw` external (see tsup.config.ts), so this never affects the shipped
// bundle. It redirects the one peer subpath the webhook handler imports at
// module load to a local fake, letting tests/webhook.test.ts run in CI without
// the ~500MB `openclaw` peer on disk.
export default defineConfig({
  test: {
    alias: {
      "openclaw/plugin-sdk/webhook-ingress": fileURLToPath(
        new URL("./tests/fakes/openclaw-plugin-sdk-webhook-ingress.ts", import.meta.url),
      ),
    },
  },
});