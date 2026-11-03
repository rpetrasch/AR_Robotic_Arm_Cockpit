// HeroUI's Tailwind plugin, loaded from app/globals.css via `@plugin` (Tailwind CSS v4).
// Primary = the project blue, secondary = the project purple (--brand-* in app/globals.css,
// docs/specs/spec_cockpit.md §5, §7.5).
import { heroui } from "@heroui/react";

export default heroui({
  themes: {
    light: { colors: { primary: { DEFAULT: "#0d6efd", foreground: "#ffffff" }, secondary: { DEFAULT: "#6f42c1", foreground: "#ffffff" } } },
    dark: { colors: { primary: { DEFAULT: "#3d8bfd", foreground: "#ffffff" }, secondary: { DEFAULT: "#8c68cd", foreground: "#ffffff" } } },
  },
});
