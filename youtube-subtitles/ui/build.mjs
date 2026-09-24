// Bundles src/menu.ts (iife, minified) and src/menu.css into index.html and writes ../app/ui/menu.html.
// The MCP host serves the view with a strict CSP (default-src 'none'), so everything must be inline.
import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../app/ui/menu.html");
const MAX_BYTES = 300_000;

// ext-apps imports zod as a whole namespace value (`import { z } from "zod/v4"`), which makes esbuild keep
// all of zod (every locale, JSON Schema conversion, ...), about 290 KB. This plugin hands ext-apps a small
// object holding only the zod functions it actually calls (found by scanning its bundle), so the rest can be
// tree-shaken. Same functions, same behavior; the build fails if the scan does not match the source.
const zodMembersForExtApps = {
  name: "zod-members-for-ext-apps",
  setup(b) {
    b.onResolve({ filter: /^zod\/v4$/ }, (args) =>
      args.namespace === "file" && /[\\/]ext-apps[\\/]dist[\\/]/.test(args.importer)
        ? { path: args.importer, namespace: "zod-members" }
        : undefined,
    );
    b.onLoad({ filter: /.*/, namespace: "zod-members" }, async (args) => {
      const src = await readFile(args.path, "utf8");
      const imports = src.match(/from\s*"zod\/v4"/g) ?? [];
      const aliases = [...src.matchAll(/import\s*\{\s*z as ([\w$]+)\s*\}\s*from\s*"zod\/v4"/g)].map((m) => m[1]);
      if (aliases.length !== imports.length) {
        throw new Error(`Unexpected zod import form in ${args.path}; update build.mjs`);
      }
      const { z } = await import("zod/v4");
      const names = new Set();
      for (const alias of aliases) {
        const esc = alias.replace(/\$/g, "\\$");
        for (const m of src.matchAll(new RegExp(`(?<![\\w$.])${esc}\\.([\\w$]+)`, "g"))) names.add(m[1]);
        // Any use of the alias other than `alias.member` (or the import itself) would need the full namespace.
        const bare = new RegExp(`(?<![\\w$.])${esc}(?![\\w$.])`, "g");
        const bareUses = (src.match(bare) ?? []).length;
        if (bareUses !== 1) throw new Error(`zod alias ${alias} is used as a value in ${args.path}; update build.mjs`);
      }
      const members = [...names].filter((n) => n in z).sort();
      const contents = `import * as zod from "zod/v4";\nexport const z = { ${members.map((n) => `${n}: zod.${n}`).join(", ")} };\n`;
      return { contents, resolveDir: here, loader: "js" };
    });
  },
};

const bundle = await build({
  entryPoints: [resolve(here, "src/menu.ts")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  sourcemap: false,
  legalComments: "none",
  charset: "ascii", // escape non-ASCII (Korean labels, vendor punctuation) so the file is plain ASCII
  write: false,
  logLevel: "warning",
  plugins: [zodMembersForExtApps],
});
const css = await build({
  entryPoints: [resolve(here, "src/menu.css")],
  bundle: true,
  minify: true,
  charset: "ascii", // escape non-ASCII (Korean labels, vendor punctuation) so the file is plain ASCII
  write: false,
  logLevel: "warning",
});

// Keep the inline code from closing its own tag early.
const js = bundle.outputFiles[0].text.replace(/<\/(script)/gi, "<\\/$1").trim();
const style = css.outputFiles[0].text.replace(/<\/(style)/gi, "<\\/$1").trim();

const template = await readFile(resolve(here, "index.html"), "utf8");
if (!template.includes("/*__MENU_CSS__*/") || !template.includes("/*__MENU_JS__*/")) {
  throw new Error("index.html is missing the /*__MENU_CSS__*/ or /*__MENU_JS__*/ placeholder");
}
// Function replacers so "$" sequences in the bundle are not treated as replacement patterns.
const html = template.replace("/*__MENU_CSS__*/", () => style).replace("/*__MENU_JS__*/", () => js);

const bytes = Buffer.byteLength(html, "utf8");
if (bytes > MAX_BYTES) throw new Error(`menu.html is ${bytes} bytes, over the ${MAX_BYTES} byte budget`);
await mkdir(dirname(out), { recursive: true });
await writeFile(out, html, "utf8");
console.log(`wrote ${out} (${bytes} bytes, ${(bytes / 1024).toFixed(1)} KiB)`);
