import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync } from "node:fs";

export default defineConfig({
  resolve: { dedupe: ["zod", "react", "react-dom"] },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: "extension-manifest",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "manifest.json",
          source: readFileSync(
            new URL("./manifest.json", import.meta.url),
            "utf8",
          ),
        });
      },
    },
  ],
  build: {
    rollupOptions: {
      input: { sidepanel: "index.html", background: "src/background/index.ts" },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "assets/[name]-[hash].js",
      },
    },
  },
});
