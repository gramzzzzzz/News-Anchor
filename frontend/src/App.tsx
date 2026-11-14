import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, useSettings } from "./api";
import { ExploreIcon, StatusIcon, Mark, NewsIcon, SettingsIcon, SourcesIcon } from "./components/icons";
import Explore from "./pages/Explore";
import Status from "./pages/Status";
import Login from "./pages/Login";
import News from "./pages/News";
import Settings from "./pages/Settings";
import Sources from "./pages/Sources";

export default function App() {
  const qc = useQueryClient();
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => api<{ configured: boolean; authenticated: boolean }>("/auth/status"), staleTime: 0 });
  useEffect(() => {
    const h = () => qc.invalidateQueries({ queryKey: ["auth"] });
    window.addEventListener("na-unauthorized", h);
    return () => window.removeEventListener("na-unauthorized", h);
  }, [qc]);
  if (auth.isPending) return <div className="login"><p className="muted">Loading…</p></div>;
  if (!auth.data?.authenticated) return <Login configured={!!auth.data?.configured} />;
  return <Shell />;
}

function Shell() {
  const settings = useSettings();
  const loc = useLocation();
  const theme = settings.get<string>("appearance.theme", "dark");
  useEffect(() => {
    const apply = () => {
      const t = theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
      document.documentElement.dataset.theme = t;
    };
    apply();
    if (theme !== "system") return;
    const m = matchMedia("(prefers-color-scheme: light)");
    m.addEventListener("change", apply);
    return () => m.removeEventListener("change", apply);
  }, [theme]);
  return (
    <div className="app">
      <nav className="rail" aria-label="Main">
        <Link className="mark" to="/" aria-label="News Anchor"><Mark /></Link>
        <Link className={`item${loc.pathname === "/" ? " active" : ""}`} to="/" title="News" aria-current={loc.pathname === "/" ? "page" : undefined}><NewsIcon /><span>News</span></Link>
        <NavLink className={({ isActive }) => `item${isActive ? " active" : ""}`} to="/explore" title="Explore"><ExploreIcon /><span>Explore</span></NavLink>
        <NavLink className={({ isActive }) => `item${isActive ? " active" : ""}`} to="/sources" title="Sources"><SourcesIcon /><span>Sources</span></NavLink>
        <NavLink className={({ isActive }) => `item${isActive ? " active" : ""}`} to="/status" title="Status"><StatusIcon /><span>Status</span></NavLink>
        <NavLink className={({ isActive }) => `item bottom${isActive ? " active" : ""}`} to="/settings" title="Settings"><SettingsIcon /><span>Settings</span></NavLink>
      </nav>
      <div className="main">
        <Routes>
          <Route path="/" element={<News />} />
          <Route path="/explore" element={<Explore />} />
          <Route path="/status" element={<Status />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}
