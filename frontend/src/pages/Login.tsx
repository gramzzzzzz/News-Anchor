import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { Mark } from "../components/icons";

export default function Login({ configured }: { configured: boolean }) {
  const qc = useQueryClient();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!configured && pw !== pw2) { setErr("The two passwords do not match."); return; }
    setBusy(true);
    try {
      await api(configured ? "/auth/login" : "/auth/setup", { method: "POST", body: { password: pw } });
      await qc.invalidateQueries({ queryKey: ["auth"] });
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <main className="login">
      <form onSubmit={submit}>
        <h1><Mark /> News Anchor</h1>
        <p className="muted">{configured ? "Enter your password to continue." : "First run. Choose a password (at least 8 characters). It protects this app only; Portfolio Tracker keeps its own."}</p>
        <div className="field"><label htmlFor="pw">Password</label><input id="pw" className="input" type="password" autoComplete={configured ? "current-password" : "new-password"} autoFocus value={pw} onChange={(e) => setPw(e.target.value)} /></div>
        {!configured && <div className="field"><label htmlFor="pw2">Repeat password</label><input id="pw2" className="input" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} /></div>}
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn" disabled={busy || pw.length < (configured ? 1 : 8)}>{configured ? "Sign in" : "Create password"}</button>
      </form>
    </main>
  );
}
