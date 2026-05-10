import { defineConfig } from "tsup";

// Multi-entry build — each module the bundled-channel-entry refers to by
// relative `./*.js` becomes a sibling output in dist/.
export default defineConfig({
  entry: [
    "src/index.ts",
    "src/channel-plugin-api.ts",
    "src/runtime-setter-api.ts",
    "src/secret-contract-api.ts",
    "src/setup-entry.ts",
  ],
  format: ["esm"],
  target: "node20",
  dts: true,
  clean: true,
  sourcemap: true,
  treeshake: true,
  external: ["openclaw"],
});
