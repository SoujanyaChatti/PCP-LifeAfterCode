"""Build the public evidence dashboard (GitLab Pages) from the trust ledger and
the latest signed evidence pack. Static HTML, no JavaScript, no external assets.

    python -m dashboard.build --ledger ledger.json --pack evidence-pack.json --out public
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil

from verifier.ledger import load, summarize

TIER = {"autonomous": ("ok", "autonomous"), "probation": ("warn", "probation"),
        "suspended": ("bad", "suspended")}

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;
--ok:#1f7a4d;--ok-bg:#e3f3ea;--bad:#b42318;--bad-bg:#fde8e6;--warn:#8a5a00;--warn-bg:#fdf1d8;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--fg:#ececea;
--muted:#a3a39e;--line:#33332f;--ok:#5fd39a;--ok-bg:#16301f;--bad:#ff8a80;--bad-bg:#3a1714;
--warn:#f3c260;--warn-bg:#352a10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.big{font-size:28px;font-weight:650}.label{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;display:block;overflow-x:auto}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}code{font-family:var(--mono);font-size:12.5px}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12.5px;font-weight:600}
.ok{color:var(--ok);background:var(--ok-bg)}.bad{color:var(--bad);background:var(--bad-bg)}
.warn{color:var(--warn);background:var(--warn-bg)}
.lie{border-left:3px solid var(--bad);padding:8px 12px;margin:8px 0;background:var(--card)}
footer{color:var(--muted);font-size:13px;margin-top:40px}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _pill(kind: str, text: str) -> str:
    return f'<span class="pill {kind}">{_e(text)}</span>'


def _decision_pill(d: str) -> str:
    return _pill("ok" if d == "PROMOTED" else "bad", d or "?")


def render(ledger: dict, pack: dict | None) -> str:
    s = summarize(ledger)
    entries = ledger.get("entries", [])
    lies = sum(1 for e in entries for c in e.get("claims", []) if c.get("lie"))
    promoted = sum(1 for e in entries if e.get("decision") == "PROMOTED")
    judged = sum(len(e.get("claims", [])) for e in entries)
    chain = _pill("ok", "intact") if s["chain_ok"] else _pill("bad", f"broken at #{s['chain_broken_at']}")

    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Polygraph</title><style>{CSS}</style></head><body><main>",
           "<h1>Polygraph</h1>",
           "<p class=sub>Every promotion carries its own proof. This page is generated from the "
           "hash-chained trust ledger and the signed evidence packs — no agent text.</p>",
           "<div class=grid>",
           f"<div class=card><div class=big>{len(entries)}</div><div class=label>pipeline runs judged</div></div>",
           f"<div class=card><div class=big>{judged}</div><div class=label>agent claims verified against evidence</div></div>",
           f"<div class=card><div class=big>{lies}</div><div class=label>false claims caught</div></div>",
           f"<div class=card><div class=big>{promoted}</div><div class=label>proven promotions</div></div>",
           f"<div class=card><div class=big>{chain}</div><div class=label>ledger hash chain</div></div>",
           "</div>"]

    out += ["<h2>Earned autonomy</h2><table><tr><th>Agent</th><th>Verified</th>"
            "<th>Caught lies</th><th>Tier</th><th>Why</th></tr>"]
    for name, a in sorted(s["agents"].items()):
        kind, label = TIER.get(a["tier"], ("warn", a["tier"]))
        out.append(f"<tr><td>{_e(name)}</td><td>{a['verified']}/{a['claims']}</td>"
                   f"<td>{a['lies']}</td><td>{_pill(kind, label)}</td><td>{_e(a['why'])}</td></tr>")
    out.append("</table>")

    if pack:
        out.append(f"<h2>Latest run {_decision_pill(pack.get('decision'))}</h2>")
        for r in pack.get("blocked_reasons", []):
            out.append(f"<div class=lie>{_e(r)}</div>")
        out.append("<table><tr><th>Gate</th><th>Verdict</th><th>Evidence</th></tr>")
        for gate, verdict in pack.get("gate_decisions", {}).items():
            recs = pack.get("evidence_by_gate", {}).get(gate, [])
            reasons = "<br>".join(f"<code>{_e(r.get('check_id'))}</code> {_e(r.get('reason'))}"
                                  for r in recs)
            out.append(f"<tr><td>{_e(gate)}</td><td>{_pill('ok' if verdict == 'PASS' else 'bad', verdict)}"
                       f"</td><td>{reasons}</td></tr>")
        out.append("</table>")
        rb = pack.get("red_vs_blue", {})
        if rb.get("exploits_attempted"):
            out.append("<h2>Red vs Blue — the same exploit, before and after</h2><table>"
                       "<tr><th>Exploit</th><th>Pre-patch</th><th>Patched</th></tr>")
            pre = rb.get("pre_patch_evidence", {})
            for eid in rb["exploits_attempted"]:
                res = rb.get("replay_post_patch", {}).get(eid, {})
                before = _pill("bad", "exploited") + " " + _e(pre[eid]) if eid in pre else _pill("ok", "clean")
                after = (_pill("bad", "STILL OPEN") if res.get("succeeded")
                         else _pill("ok", "closed") + " " + _e(res.get("evidence", "")))
                out.append(f"<tr><td><code>{_e(eid)}</code></td><td>{before}</td><td>{after}</td></tr>")
            out.append("</table>")
        st = pack.get("verifier_self_test") or {}
        if st:
            out.append(f"<h2>Who verified the verifier</h2><p>Planted {st.get('planted')} known lies; "
                       f"caught {st.get('caught')}. "
                       f"{_pill('ok', 'verifier alive') if st.get('verifier_alive') else _pill('bad', 'VERIFIER BROKEN')}"
                       f" Fingerprint <code>{_e(str(pack.get('verifier_fingerprint'))[:16])}…</code></p>")
        for lie in pack.get("caught_false_claims", []):
            out.append(f"<div class=lie><b>Caught false claim</b> · <code>{_e(lie.get('check_id'))}</code>: "
                       f"{_e(lie.get('reason'))}</div>")

    out += ["<h2>Run history (newest first)</h2><table><tr><th>#</th><th>Decision</th>"
            "<th>Commit</th><th>Claims</th><th>Entry hash</th><th>Prev</th></tr>"]
    for i, e in reversed(list(enumerate(entries, 1))):
        counts: dict[str, list[int]] = {}
        for c in e.get("claims", []):
            n = counts.setdefault(f"{c['agent']}:{c['type']}", [0, 0])
            n[0] += 1
            n[1] += bool(c.get("lie"))
        claims = ", ".join(f"{k}{f' ×{n}' if n > 1 else ''}{f' ⚠ {lies} lie' if lies else ''}"
                           for k, (n, lies) in counts.items())
        out.append(f"<tr><td>{i}</td><td>{_decision_pill(e.get('decision'))}</td>"
                   f"<td><code>{_e(str(e.get('run', {}).get('commit_sha') or '')[:10])}</code></td>"
                   f"<td>{_e(claims)}</td><td><code>{_e(e['hash'][:12])}</code></td>"
                   f"<td><code>{_e(e['prev_hash'][:12])}</code></td></tr>")
    out += ["</table>",
            "<footer>In CI, every pack is signed keyless with Sigstore cosign and verified in a "
            "separate job pinned to this project's pipeline identity. "
            "Raw data: <a href=ledger.json>ledger.json</a>"
            + (" · <a href=evidence-pack.json>latest evidence-pack.json</a>" if pack else "")
            + "</footer></main></body></html>"]
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", default="ledger.json")
    p.add_argument("--pack", default=None)
    p.add_argument("--out", default="public")
    args = p.parse_args(argv)

    ledger = load(args.ledger)
    pack = None
    if args.pack and os.path.exists(args.pack):
        with open(args.pack) as f:
            pack = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w") as f:
        f.write(render(ledger, pack))
    with open(os.path.join(args.out, "ledger.json"), "w") as f:
        json.dump(ledger, f, indent=2)
    if pack:
        shutil.copy(args.pack, os.path.join(args.out, "evidence-pack.json"))
    print(f"dashboard -> {args.out}/index.html ({len(ledger.get('entries', []))} runs)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
