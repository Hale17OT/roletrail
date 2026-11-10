import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
const root = process.cwd();
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
  );
}
const files = [
  ...walk(join(root, "src")).filter((f) => /\.(vue|js|mjs|css|json)$/.test(f)),
  ...["package.json", "package-lock.json", "index.html", "vite.config.js"].map(
    (p) => join(root, p),
  ),
];
const hash = createHash("sha256");
for (const file of files.sort((a, b) =>
  relative(root, a).replaceAll("\\", "/") <
  relative(root, b).replaceAll("\\", "/")
    ? -1
    : 1,
)) {
  hash.update(relative(root, file).replaceAll("\\", "/") + "\0");
  hash.update(readFileSync(file));
}
writeFileSync(
  join(root, "dist/build-info.json"),
  JSON.stringify({
    builtAt: new Date().toISOString(),
    sourceSha256: hash.digest("hex"),
  }),
);
