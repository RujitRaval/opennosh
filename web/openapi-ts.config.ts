import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./lib/generated/openapi.json",
  output: {
    path: "./lib/generated/client",
    postProcess: [],
  },
  plugins: [
    {
      name: "@hey-api/client-fetch",
      bundle: true,
    },
    "@hey-api/typescript",
    "@hey-api/sdk",
  ],
});
