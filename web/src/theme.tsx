import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

type ThemePreference = "light" | "dark" | "system";
const STORAGE_KEY = "launcher.theme";

function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    const current = document.documentElement.dataset.themePreference;
    if (current === "light" || current === "dark") return current;
  }
  return "system";
}

function applyTheme(preference: ThemePreference) {
  const dark =
    preference === "dark" ||
    (preference === "system" &&
      matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themePreference = preference;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", dark ? "#111a28" : "#f5f7fb");
}

export function ThemeSelect() {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);
  useEffect(() => {
    applyTheme(preference);
    const media = matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => applyTheme(preference);
    const onStorageChange = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null)
        setPreference(readPreference());
    };
    media.addEventListener("change", onSystemChange);
    window.addEventListener("storage", onStorageChange);
    return () => {
      media.removeEventListener("change", onSystemChange);
      window.removeEventListener("storage", onStorageChange);
    };
  }, [preference]);

  const Icon =
    preference === "system" ? Monitor : preference === "dark" ? Moon : Sun;
  return (
    <label className="theme-select">
      <Icon size={15} aria-hidden="true" />
      <select
        aria-label="外观"
        value={preference}
        onChange={(event) => {
          const next = event.target.value as ThemePreference;
          try {
            localStorage.setItem(STORAGE_KEY, next);
          } catch {
            // Keep the selected theme for this page even if it cannot be saved.
          }
          applyTheme(next);
          setPreference(next);
        }}
      >
        <option value="system">跟随系统</option>
        <option value="light">浅色模式</option>
        <option value="dark">深色模式</option>
      </select>
    </label>
  );
}
