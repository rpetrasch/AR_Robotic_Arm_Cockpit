"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

/** Light / dark follows the OS unless the user picks one in the user menu (§7.1). The choice
 * is a per-browser convenience in localStorage; THEME_SCRIPT applies it before the first paint. */
export type ThemeMode = "system" | "light" | "dark";
const KEY = "ar3cockpit.theme";

export const THEME_SCRIPT = `(function(){try{var m=localStorage.getItem("${KEY}")||"system";
var d=m==="dark"||(m==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);
document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;

const ThemeContext = createContext<{ mode: ThemeMode; choose: (mode: ThemeMode) => void }>({
  mode: "system",
  choose: () => {},
});

function read(): ThemeMode {
  try {
    const m = localStorage.getItem(KEY);
    return m === "light" || m === "dark" ? m : "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("system");
  useEffect(() => setMode(read()), []);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = mode === "dark" || (mode === "system" && media.matches);
      document.documentElement.classList.toggle("dark", dark);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [mode]);
  const choose = useCallback((next: ThemeMode) => {
    setMode(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // private window: the choice lasts for this page only
    }
  }, []);
  return <ThemeContext.Provider value={{ mode, choose }}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
