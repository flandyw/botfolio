import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, lazyPlugins, type Plugin } from "vite-plus";
import { createAlpacaBridge } from "./server/alpaca-bridge.ts";

function alpacaBridgePlugin(): Plugin {
  return {
    name: "botfolio-alpaca-bridge",
    configureServer(server) {
      const bridge = createAlpacaBridge();
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/alpaca")) {
          next();
          return;
        }
        void bridge.handle(req, res).then((handled) => {
          if (!handled) next();
        });
      });
      server.httpServer?.once("close", () => bridge.close());
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  fmt: {},
  lint: {
    plugins: ["react", "typescript", "oxc"],
    rules: {
      "react/rules-of-hooks": "error",
      "react/only-export-components": [
        "warn",
        {
          allowConstantExport: true,
        },
      ],
      "vite-plus/prefer-vite-plus-imports": "error",
    },
    options: {
      typeAware: true,
      typeCheck: true,
    },
    jsPlugins: [
      {
        name: "vite-plus",
        specifier: "vite-plus/oxlint-plugin",
      },
    ],
  },
  plugins: [alpacaBridgePlugin(), tailwindcss(), ...(lazyPlugins(() => [react()]) ?? [])],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy: { "/api": "http://127.0.0.1:8600" } },
});
