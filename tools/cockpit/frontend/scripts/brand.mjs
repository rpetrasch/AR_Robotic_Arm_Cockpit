// Build step (npm run build): PNG exports of the logo for the web manifest
// (docs/specs/spec_cockpit.md §7.5). 512/192 px from the mark, 32 px from the simplified favicon.
// Output goes to public/icons/ (generated, not committed).
import { access, mkdir, writeFile } from "node:fs/promises";
import sharp from "sharp";

const brand = new URL("../public/brand/", import.meta.url);
const icons = new URL("../public/icons/", import.meta.url);
await mkdir(icons, { recursive: true });

const sizes = [
  ["ar3-cockpit-mark.svg", 512],
  ["ar3-cockpit-mark.svg", 192],
  ["favicon.svg", 32],
];
for (const [file, size] of sizes) {
  await sharp(new URL(file, brand).pathname, { density: 600 })
    .resize(size, size)
    .png()
    .toFile(new URL(`icon-${size}.png`, icons).pathname);
}

const manifest = {
  name: "AR3 Cockpit",
  short_name: "AR3 Cockpit",
  start_url: "/environment/",
  display: "standalone",
  background_color: "#ffffff",
  theme_color: "#0d6efd",
  icons: [512, 192, 32].map((s) => ({ src: `/icons/icon-${s}.png`, sizes: `${s}x${s}`, type: "image/png" })),
};
await writeFile(new URL("../public/manifest.webmanifest", import.meta.url), JSON.stringify(manifest, null, 2) + "\n");
// Optional login media (§7.3): the page asks only for files that exist, so a missing video is
// not a 404 on every visit. Add public/login/real.mp4 (+ poster.jpg) and rebuild.
const login = new URL("../public/login/", import.meta.url);
const exists = (f) => access(new URL(f, login)).then(() => true, () => false);
const media = { realVideo: await exists("real.mp4"), poster: await exists("poster.jpg") };
await writeFile(new URL("media.json", login), JSON.stringify(media) + "\n");
console.log(`brand: icons 512/192/32 + manifest.webmanifest; login media ${JSON.stringify(media)}`);
