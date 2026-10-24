"""Render the animated run replay (replay.html) from real evidence packs.

The page replays what a pipeline run actually recorded: each agent's claim, the
verifier's verdict on it, the Red Team's exploits against the pre-patch twin, the
same exploits replayed against the hardened build, and the final decision. Every
value shown comes from the pack; nothing is staged. Values are inserted as text,
never as HTML, because evidence strings can contain markup (the XSS probe's does).
"""
from __future__ import annotations

import html
import json

GATE_ORDER = ["verifier.canary", "plan.ready", "code.ready", "pipeline.green", "tests.pass",
              "image.built", "vuln.fixed", "deploy.healthy"]

# gate -> (title, DevSecOps stage, agent that claims it)
GATE_INFO = {
    "verifier.canary": ("Verifier self-test", "Govern", "verifier"),
    "plan.ready": ("Plan", "Plan", "planner"),
    "code.ready": ("Merge request", "Create", "builder"),
    "pipeline.green": ("Pipeline", "Verify", "pipeline_fixer"),
    "tests.pass": ("Tests", "Verify", "builder"),
    "image.built": ("Image", "Package", "release"),
    "vuln.fixed": ("Security fixes", "Secure", "blueteam"),
    "deploy.healthy": ("Production", "Release", "release"),
}

EXPLOITS = {
    "v1_sql_injection": ("V1", "SQL injection"),
    "v2_reflected_xss": ("V2", "Reflected XSS"),
    "v3_idor": ("V3", "IDOR"),
    "v4_admin_no_auth": ("V4", "Admin without auth"),
    "v5_stacktrace_leak": ("V5", "Stack-trace leak"),
    "v6_vulnerable_dependency": ("V6", "Vulnerable dependency"),
    "v7_prompt_injection": ("V7", "Prompt injection"),
}

MAX_CHECKS = 60


def _gate_order(pack: dict) -> list[str]:
    seen = list(pack.get("gate_decisions", {})) + list(pack.get("required_gates") or [])
    ordered = [g for g in GATE_ORDER if g in seen]
    return ordered + [g for g in dict.fromkeys(seen) if g not in ordered]


def _run_label(run: dict) -> str:
    if run.get("kind") == "rehearsal":
        return f"Rehearsal · {run.get('scenario', '')}"
    if run.get("pipeline_id"):
        return f"Pipeline #{run['pipeline_id']}" + (f" · {run['ref']}" if run.get("ref") else "")
    return "Latest run"


def slim(pack: dict, label: str | None = None) -> dict:
    """Reduce a pack to what the replay animates. Pure data, no markup."""
    run = pack.get("run") or {}
    claims: dict[str, list[dict]] = {}
    for c in pack.get("claims", []):
        claims.setdefault(c.get("type", ""), []).append(c)

    st = pack.get("verifier_self_test") or {}
    gates = []
    for g in _gate_order(pack):
        title, stage, agent = GATE_INFO.get(g, (g, "", ""))
        cl = claims.get(g, [])
        recs = pack.get("evidence_by_gate", {}).get(g, [])
        checks = [{"id": r.get("check_id", ""), "verdict": r.get("verdict", ""),
                   "reason": r.get("reason", ""),
                   "lie": "claim_mismatch" in (r.get("tags") or [])} for r in recs]
        gates.append({
            "gate": g, "title": title, "stage": stage,
            "agent": (cl[0].get("agent") if cl else None) or agent,
            "claims": len(cl),
            "statement": cl[0].get("statement", "") if cl else "",
            "asserted": cl[0].get("asserted", {}) if len(cl) == 1 else {},
            "verdict": pack.get("gate_decisions", {}).get(g) or "NO EVIDENCE",
            "passed": sum(1 for c in checks if c["verdict"] == "PASS"),
            "total": len(checks),
            "checks": [c for c in checks if c["verdict"] != "PASS" or c["lie"]][:MAX_CHECKS],
        })
        if g == "verifier.canary" and st and not recs:
            # The canary's evidence is the self-test block itself, not a gate record.
            gates[-1].update(
                verdict="PASS" if st.get("verifier_alive") else "FAIL",
                passed=st.get("caught") or 0, total=st.get("planted") or 0,
                statement=f"Planted {st.get('planted')} known lies to prove the verifier still catches them.",
                checks=[{"id": r.get("canary", ""), "verdict": "FAIL", "lie": False,
                         "reason": f"missed planted lie {r.get('canary')}"}
                        for r in st.get("results", []) if not r.get("caught")])

    rb = pack.get("red_vs_blue") or {}
    pre = rb.get("pre_patch_evidence") or {}
    post = rb.get("replay_post_patch") or {}
    exploits = []
    for eid in rb.get("exploits_attempted") or []:
        vid, name = EXPLOITS.get(eid, ("", eid))
        res = post.get(eid) or {}
        exploits.append({
            "id": eid, "vuln": res.get("vuln_id") or vid, "name": name,
            "found": eid in pre, "pre": pre.get(eid, "no weakness reachable"),
            "replayed": bool(res), "still_open": bool(res.get("succeeded")),
            "post": res.get("evidence", ""),
        })

    return {
        "label": label or _run_label(run),
        "decision": pack.get("decision", "?"),
        "run": {k: run.get(k) for k in ("kind", "scenario", "issue_iid", "mr_iid",
                                         "commit_sha", "pipeline_id", "pipeline_url", "ref")},
        "signed": run.get("kind") not in (None, "rehearsal"),
        "self_test": {"planted": st.get("planted"), "caught": st.get("caught"),
                      "alive": bool(st.get("verifier_alive"))} if st else None,
        "gates": gates,
        "exploits": exploits,
        "lies": [{"check": l.get("check_id", ""), "reason": l.get("reason", "")}
                 for l in pack.get("caught_false_claims", [])],
        "blocked": pack.get("blocked_reasons", []),
        "hash": pack.get("content_sha256", ""),
    }


def _json_for_script(obj) -> str:
    # Safe inside <script type="application/json">: no "</script>" or "<!--" can close it.
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render(packs: list[tuple[str | None, dict]]) -> str:
    data = [slim(p, label) for label, p in packs]
    return (PAGE.replace("/*CSS*/", CSS).replace("/*JS*/", JS)
            .replace("__COUNT__", html.escape(str(len(data))))
            .replace("__DATA__", _json_for_script(data)))


CSS = r"""
:root{--bg:#f6f5f1;--panel:#fff;--fg:#1c1c1a;--muted:#6b6a64;--line:#e3e1da;
--red:#e5484d;--red-bg:#fde8e6;--blue:#2f6fed;--blue-bg:#e3ecfd;--ok:#1f8a55;--ok-bg:#e1f4ea;
--warn:#a16b00;--warn-bg:#fcf0d4;--trace:#1f8a55;--grid:#ecebe5;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--panel:#1c1c1a;--fg:#ecebe7;--muted:#a3a29b;
--line:#32312d;--red:#ff6b6f;--red-bg:#3b1517;--blue:#6f9bff;--blue-bg:#16233f;--ok:#5bd497;--ok-bg:#13301f;
--warn:#f2c35c;--warn-bg:#352a10;--trace:#5bd497;--grid:#24231f}}
*{box-sizing:border-box}
html,body{overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px;overflow-wrap:anywhere}
a{color:var(--blue)}
header.top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between}
h1{font-size:24px;margin:0}h1 small{font-weight:500;color:var(--muted);font-size:15px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 0}
.tab{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:99px;
padding:5px 14px;font:inherit;font-size:13.5px;cursor:pointer}
.tab[aria-selected=true]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:8px;
padding:6px 12px;font:inherit;font-size:13.5px;cursor:pointer;min-width:40px}
.btn.primary{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.btn:focus-visible,.tab:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.meta{color:var(--muted);font-size:13px;margin:8px 0 0;font-family:var(--mono)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:14px}
.trace{padding:10px 16px}
.trace-head{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);
text-transform:uppercase;letter-spacing:.06em}
.trace svg{width:100%;height:70px;display:block}
.narr{font-size:17px;font-weight:600;min-height:28px;margin:14px 2px 0}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:14px}
.layout.solo{grid-template-columns:1fr}
.layout>section{min-width:0}
@media (max-width:860px){.layout{grid-template-columns:1fr}}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 10px}
.gates{display:flex;flex-direction:column;gap:10px}
.gate{position:relative;border:1px solid var(--line);border-radius:12px;padding:10px 12px;
display:grid;grid-template-columns:44px 1fr auto;gap:10px;align-items:center;opacity:.45;
transition:opacity .3s,border-color .3s,background .3s;overflow:hidden}
.gate.active{opacity:1;border-color:var(--blue)}
.gate.done{opacity:1}
.gate.pass{border-color:var(--ok)}.gate.fail{border-color:var(--red);background:var(--red-bg)}
.gate>div{min-width:0}
.gate .who{font-size:12px;color:var(--muted)}
.gate .title{font-weight:650}
.gate .claim{font-size:13px;margin-top:2px}
.gate .checks{font-size:12.5px;color:var(--muted);margin-top:4px}
.gate .checks div{margin-top:2px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700;white-space:nowrap}
.pill.wait{color:var(--muted);background:var(--grid)}
.pill.run{color:var(--blue);background:var(--blue-bg)}
.pill.ok{color:var(--ok);background:var(--ok-bg)}
.pill.bad{color:var(--red);background:var(--red-bg)}
.pill.warn{color:var(--warn);background:var(--warn-bg)}
.status{display:flex;flex-direction:column;align-items:flex-end;gap:10px}
.stamp{display:none;transform:rotate(-10deg) scale(2.4);border:3px solid var(--red);color:var(--red);
font-weight:800;letter-spacing:.06em;padding:1px 8px;border-radius:6px;background:var(--panel);
opacity:0;font-size:13px;white-space:nowrap}
.stamp.show{display:block;animation:stamp .45s cubic-bezier(.2,1.6,.4,1) forwards}
@keyframes stamp{to{opacity:1;transform:rotate(-10deg) scale(1)}}
.lie-box{margin-top:6px;font-size:13px;border-left:3px solid var(--red);padding:4px 8px;background:var(--panel)}
.arena{position:relative;height:190px;border-radius:12px;background:
linear-gradient(var(--grid) 1px,transparent 1px) 0 0/24px 24px,
linear-gradient(90deg,var(--grid) 1px,transparent 1px) 0 0/24px 24px;border:1px solid var(--line);overflow:hidden}
.bot{position:absolute;bottom:22px;width:78px;text-align:center;font-size:12px;font-weight:700}
.bot svg{width:70px;height:84px;display:block;margin:0 auto}
.bot.red{left:4%;color:var(--red)}.bot.blue{right:4%;color:var(--blue)}
.bot.busy svg{animation:bob .6s ease-in-out infinite alternate}
@keyframes bob{to{transform:translateY(-6px)}}
.bot .eye{animation:blink 4s infinite;transform-origin:center;transform-box:fill-box}
@keyframes blink{0%,94%,100%{transform:scaleY(1)}97%{transform:scaleY(.1)}}
.server{position:absolute;left:50%;bottom:20px;transform:translateX(-50%);width:120px;text-align:center;font-size:12px}
.server .box{height:96px;border-radius:10px;border:2px solid var(--line);background:var(--panel);
display:flex;flex-direction:column;justify-content:center;gap:6px;padding:0 14px;position:relative}
.server .box i{display:block;height:8px;border-radius:4px;background:var(--grid)}
.server .box i::after{content:"";display:block;width:8px;height:8px;border-radius:4px;background:var(--ok)}
.server.vuln .box{border-color:var(--red)}
.server.hard .box{border-color:var(--blue);box-shadow:0 0 0 6px var(--blue-bg)}
.server .name{margin-top:20px;font-weight:700}
.server.hit .box{animation:shake .3s}
@keyframes shake{25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
.shield{position:absolute;inset:-14px;border-radius:18px;border:3px solid var(--blue);opacity:0;transition:opacity .4s}
.server.hard .shield{opacity:.9}
.bolt{position:absolute;bottom:80px;width:16px;height:16px;border-radius:8px;pointer-events:none}
.bolt.r{background:var(--red);box-shadow:0 0 12px var(--red)}
.exploits{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:12px}
.xp{border:1px solid var(--line);border-radius:10px;padding:8px 10px;font-size:12.5px;transition:background .3s,border-color .3s}
.xp b{display:block;font-size:13px}
.xp .ev{color:var(--muted);margin-top:3px;font-size:12px;min-height:1em}
.xp.found{border-color:var(--red);background:var(--red-bg)}
.xp.closed{border-color:var(--ok);background:var(--ok-bg)}
.xp.flip{animation:flip .35s}
@keyframes flip{50%{transform:rotateX(90deg)}}
.verdict{text-align:center;padding:22px 16px}
.big-stamp{display:inline-block;font-size:30px;font-weight:850;letter-spacing:.1em;padding:6px 22px;
border:4px solid currentColor;border-radius:10px;transform:rotate(-4deg) scale(1.8);opacity:0}
.big-stamp.show{animation:bigstamp .5s cubic-bezier(.2,1.6,.4,1) forwards}
@keyframes bigstamp{to{opacity:1;transform:rotate(-4deg) scale(1)}}
.big-stamp.ok{color:var(--ok)}.big-stamp.bad{color:var(--red)}
.reasons{list-style:none;padding:0;margin:14px 0 0;font-size:14px}
.reasons li{margin:4px 0}
.seal{margin-top:12px;font-size:13px;color:var(--muted)}
.seal code{font-family:var(--mono);font-size:12px}
.hidden{display:none}
footer{color:var(--muted);font-size:13px;margin-top:28px}
@media (max-width:560px){.gate{grid-template-columns:1fr auto}.gate>div:first-child{display:none}.bot{width:64px}.bot svg{width:56px;height:68px}.server{width:96px}.stamp{font-size:11px;padding:1px 5px}.big-stamp{font-size:24px}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;
animation-iteration-count:1!important;transition-duration:.01ms!important}}
"""

PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Polygraph · Run replay</title><style>/*CSS*/</style></head><body><main>
<header class=top>
  <h1>Polygraph <small>· run replay</small></h1>
  <div class=controls>
    <button class="btn primary" id=play aria-label="Play">▶ Play</button>
    <button class=btn id=next aria-label="Next step">Next ›</button>
    <button class=btn id=restart aria-label="Restart">↺</button>
    <button class=btn id=speed aria-label="Playback speed">1×</button>
  </div>
</header>
<div class=tabs role=tablist id=tabs></div>
<p class=meta id=meta></p>

<section class="panel trace" aria-label="Verifier trace">
  <div class=trace-head><span>Verifier trace</span><span id=trace-state>calm</span></div>
  <svg viewBox="0 0 1000 70" preserveAspectRatio=none id=trace aria-hidden=true></svg>
</section>
<p class=narr id=narr aria-live=polite>Press Play to replay this run from its signed evidence.</p>

<div class=layout id=layout>
  <section class=panel aria-label="Gates">
    <h2>Gates · agent claim vs evidence</h2>
    <div class=gates id=gates></div>
  </section>
  <section class=panel id=arena-panel aria-label="Red Team versus Blue Team">
    <h2>Red Team vs Blue Team · same exploits, before and after</h2>
    <div class=arena id=arena>
      <div class="bot red" id=bot-red>__RED__<div>Red Team</div></div>
      <div class=server id=server><div class=box><span class=shield></span><i></i><i></i><i></i></div>
        <div class=name id=server-name>pre-patch twin</div></div>
      <div class="bot blue" id=bot-blue>__BLUE__<div>Blue Team</div></div>
    </div>
    <div class=exploits id=exploits></div>
  </section>
</div>

<section class="panel verdict" id=verdict aria-label="Decision">
  <div class=big-stamp id=stamp></div>
  <ul class=reasons id=reasons></ul>
  <div class=seal id=seal></div>
</section>

<footer>Every value on this page is read from the run's evidence pack (<a href=evidence-pack.json>raw JSON</a>);
the animation only paces it. Runs shown: __COUNT__. <a href=index.html>Back to the dashboard</a></footer>
</main>
<script type=application/json id=data>__DATA__</script>
<script>/*JS*/</script></body></html>"""


def _bot(color: str, kind: str) -> str:
    accent = ('<path d="M54 46 l14 -6" stroke="{c}" stroke-width="5" stroke-linecap="round"/>'
              if kind == "red" else
              '<path d="M8 40 q-6 14 4 26 q10 -12 4 -26 z" fill="{c}" opacity=".85"/>').format(c=color)
    return ('<svg viewBox="0 0 70 84" aria-hidden="true">'
            f'<line x1="35" y1="4" x2="35" y2="14" stroke="{color}" stroke-width="3"/>'
            f'<circle cx="35" cy="5" r="4" fill="{color}"/>'
            f'<rect x="12" y="14" width="46" height="34" rx="12" fill="{color}"/>'
            '<rect x="18" y="22" width="34" height="16" rx="8" fill="#fff" opacity=".92"/>'
            '<circle class="eye" cx="28" cy="30" r="4" fill="#1c1c1a"/>'
            '<circle class="eye" cx="42" cy="30" r="4" fill="#1c1c1a"/>'
            f'<rect x="18" y="50" width="34" height="24" rx="8" fill="{color}" opacity=".85"/>'
            f'<rect x="22" y="74" width="8" height="8" rx="3" fill="{color}"/>'
            f'<rect x="40" y="74" width="8" height="8" rx="3" fill="{color}"/>'
            f'{accent}</svg>')


PAGE = PAGE.replace("__RED__", _bot("#e5484d", "red")).replace("__BLUE__", _bot("#2f6fed", "blue"))

JS = r"""
(() => {
const RUNS = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag);
  if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const AGENT = {planner:'Planner', builder:'Builder', pipeline_fixer:'Pipeline Fixer',
  release:'Release', blueteam:'Blue Team', redteam:'Red Team', reporter:'Reporter', verifier:'Verifier'};
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

let run = 0, steps = [], i = 0, timer = null, playing = false, speed = 1;

/* ---- polygraph trace: calm line, blips on checks, red spike on a caught lie ---- */
let pts = [], ticker = null, spikeQ = [];
function tick() {
  let y = 35 + (Math.random() - .5) * 4, c = 'ok';
  if (spikeQ.length) { const s = spikeQ.shift(); y = s[0]; c = s[1]; }
  pts.push([y, c]); if (pts.length > 200) pts.shift(); drawTrace();
}
function drawTrace() {
  const svg = $('trace'); svg.textContent = '';
  const dx = 1000 / 199; let seg = [], col = null;
  const flush = () => { if (seg.length > 1) { const p = document.createElementNS('http://www.w3.org/2000/svg','polyline');
    p.setAttribute('points', seg.join(' ')); p.setAttribute('fill','none');
    p.setAttribute('stroke', col === 'lie' ? 'var(--red)' : 'var(--trace)');
    p.setAttribute('stroke-width', col === 'lie' ? 3 : 2); p.setAttribute('vector-effect','non-scaling-stroke');
    svg.appendChild(p); } };
  pts.forEach(([y, c], k) => { const xy = (k * dx).toFixed(1) + ',' + y.toFixed(1);
    if (c !== col) { flush(); seg = seg.length ? [seg[seg.length - 1]] : []; col = c; } seg.push(xy); });
  flush();
}
const blip = () => spikeQ.push([22,'ok'],[48,'ok'],[35,'ok']);
function lieSpike() { spikeQ.push([30,'lie'],[2,'lie'],[68,'lie'],[4,'lie'],[66,'lie'],[10,'lie'],[60,'lie'],[35,'lie']);
  $('trace-state').textContent = 'lie detected'; }

/* ---- scene construction ---- */
function pill(text, kind) { return el('span', 'pill ' + kind, text); }

function build() {
  const R = RUNS[run];
  $('meta').textContent = [R.label,
    R.run.issue_iid ? 'issue #' + R.run.issue_iid : null,
    R.run.mr_iid ? 'MR !' + R.run.mr_iid : null,
    R.run.commit_sha ? 'commit ' + String(R.run.commit_sha).slice(0, 10) : null].filter(Boolean).join(' · ');
  const gates = $('gates'); gates.textContent = '';
  R.gates.forEach((g, k) => {
    const card = el('div', 'gate'); card.id = 'g' + k;
    const icon = el('div'); icon.appendChild(el('div', 'who', g.stage)); card.appendChild(icon);
    const body = el('div');
    body.appendChild(el('div', 'title', g.title));
    body.appendChild(el('div', 'who', (AGENT[g.agent] || g.agent || '') + (g.claims > 1 ? ' · ' + g.claims + ' claims' : '')));
    const claim = el('div', 'claim'); claim.id = 'gc' + k; body.appendChild(claim);
    const checks = el('div', 'checks'); checks.id = 'gk' + k; body.appendChild(checks);
    card.appendChild(body);
    const col = el('div', 'status'); const st = el('div'); st.id = 'gs' + k; st.appendChild(pill('waiting', 'wait'));
    const stamp = el('div', 'stamp', 'CAUGHT LIE'); stamp.id = 'gx' + k;
    col.appendChild(st); col.appendChild(stamp); card.appendChild(col);
    gates.appendChild(card);
  });
  $('arena-panel').classList.toggle('hidden', !R.exploits.length);
  $('layout').classList.toggle('solo', !R.exploits.length);
  const xs = $('exploits'); xs.textContent = '';
  R.exploits.forEach((x, k) => {
    const c = el('div', 'xp'); c.id = 'x' + k;
    c.appendChild(el('b', null, (x.vuln ? x.vuln + ' · ' : '') + x.name));
    const ev = el('div', 'ev', 'queued'); ev.id = 'xe' + k; c.appendChild(ev); xs.appendChild(c);
  });
  $('server').className = 'server'; $('server-name').textContent = 'pre-patch twin';
  $('bot-red').classList.remove('busy'); $('bot-blue').classList.remove('busy');
  $('stamp').className = 'big-stamp'; $('stamp').textContent = '';
  $('reasons').textContent = ''; $('seal').textContent = '';
  $('trace-state').textContent = 'calm'; pts = []; spikeQ = []; for (let k = 0; k < 200; k++) pts.push([35, 'ok']);
  drawTrace();
  steps = script(R); i = 0;
  say('Press Play to replay this run from its signed evidence.');
}

function say(t) { $('narr').textContent = t; }
function setStatus(k, text, kind) { const s = $('gs' + k); s.textContent = ''; s.appendChild(pill(text, kind)); }

function bolt(fromRed, bounce) {
  if (reduce) return;
  const arena = $('arena'), b = el('div', 'bolt r');
  arena.appendChild(b);
  const w = arena.clientWidth, start = w * .04 + 70, hit = w / 2 - 70;
  const frames = bounce
    ? [{left: start + 'px', opacity: 1}, {left: (hit - 24) + 'px', opacity: 1, offset: .6}, {left: (hit - 90) + 'px', opacity: 0}]
    : [{left: start + 'px', opacity: 1}, {left: hit + 'px', opacity: 1}];
  b.animate(frames, {duration: 650 / speed, easing: 'ease-in'}).onfinish = () => {
    b.remove(); if (!bounce) { const s = $('server'); s.classList.add('hit'); setTimeout(() => s.classList.remove('hit'), 320); } };
}

/* ---- the run as a list of timed steps, all driven by pack data ---- */
function script(R) {
  const S = [];
  const add = (ms, fn) => S.push({ms, fn});
  add(900, () => say((R.run.issue_iid ? 'Issue #' + R.run.issue_iid + ' enters the pipeline. ' : '') +
    'Agents do the work; the verifier only believes evidence.'));
  if (R.self_test && !R.gates.some(g => g.gate === 'verifier.canary')) add(1300, () => { say('First the verifier tests itself: ' + R.self_test.planted +
    ' planted lies, ' + R.self_test.caught + ' caught.'); for (let k = 0; k < 3; k++) blip(); });

  const gateStep = k => {
    const g = R.gates[k], card = $('g' + k);
    add(1100, () => {
      card.classList.add('active'); setStatus(k, 'checking', 'run');
      const who = AGENT[g.agent] || g.agent || 'An agent';
      $('gc' + k).textContent = !g.statement ? (g.verdict === 'NO EVIDENCE' ? '' : who + ' made no claim; CI recorded the facts.')
        : g.gate === 'verifier.canary' ? g.statement
        : g.claims > 1 ? who + ' makes ' + g.claims + ' claims, e.g. "' + g.statement + '"'
        : who + ' claims: "' + g.statement + '"';
      say(g.gate === 'verifier.canary' ? 'Before judging anyone, the verifier tests itself with planted lies…' :
        g.verdict === 'NO EVIDENCE' ? g.title + ': this gate never produced evidence.' :
        g.title + ': ' + (g.statement ? who + ' makes a claim. Checking it against evidence…' : 'checking the evidence…'));
      blip();
    });
    add(1300, () => {
      const lie = g.checks.find(c => c.lie);
      const kc = $('gk' + k); kc.textContent = '';
      if (g.total) kc.appendChild(el('div', null, g.passed + '/' + g.total + ' checks passed'));
      g.checks.filter(c => c.verdict !== 'PASS').slice(0, 3).forEach(c => kc.appendChild(el('div', null, '✕ ' + c.reason)));
      card.classList.remove('active'); card.classList.add('done');
      if (g.verdict === 'PASS') { card.classList.add('pass'); setStatus(k, 'PASS', 'ok');
        say(g.gate === 'verifier.canary' ? 'Verifier self-test: ' + g.passed + ' of ' + g.total + ' planted lies caught. The judge is alive.'
          : g.title + ': every check matches the evidence.'); }
      else if (g.verdict === 'NO EVIDENCE') { card.classList.add('fail'); setStatus(k, 'NO EVIDENCE', 'warn');
        say(g.title + ': a gate that never ran does not pass. Fail closed.'); }
      else { card.classList.add('fail'); setStatus(k, g.verdict, 'bad');
        if (lie) { $('gx' + k).classList.add('show'); lieSpike();
          const box = el('div', 'lie-box', 'Claim vs evidence: ' + lie.reason); kc.appendChild(box);
          say('Caught lie. ' + lie.reason + '. The gate locks.'); }
        else say(g.title + ': the evidence does not support the claim. Blocked.'); }
    });
  };

  const arena = () => {
    if (!R.exploits.length) return;
    add(1000, () => { $('bot-red').classList.add('busy'); $('server').classList.add('vuln');
      say('Red Team attacks the deliberately vulnerable pre-patch twin with ' + R.exploits.length + ' exploits.'); });
    R.exploits.forEach((x, k) => add(780, () => {
      bolt(true, !x.found);
      setTimeout(() => { const c = $('x' + k); c.classList.add('flip');
        if (x.found) c.classList.add('found');
        $('xe' + k).textContent = (x.found ? 'FOUND · ' : 'clean · ') + x.pre; }, 420 / speed);
    }));
    add(1300, () => { $('bot-red').classList.remove('busy'); $('bot-blue').classList.add('busy');
      $('server').classList.remove('vuln'); $('server').classList.add('hard');
      $('server-name').textContent = 'hardened build';
      say('Blue Team ships its patch. The verifier, not the Blue Team, replays the same exploits.'); });
    R.exploits.forEach((x, k) => { if (!x.replayed) return; add(780, () => {
      bolt(true, !x.still_open);
      setTimeout(() => { const c = $('x' + k); c.classList.remove('flip'); void c.offsetWidth; c.classList.add('flip');
        c.classList.remove('found'); c.classList.add(x.still_open ? 'found' : 'closed');
        $('xe' + k).textContent = (x.still_open ? 'STILL OPEN · ' : 'CLOSED · ') + x.post;
        if (x.still_open) lieSpike(); }, 420 / speed);
    }); });
    add(900, () => { $('bot-blue').classList.remove('busy');
      const open = R.exploits.filter(x => x.still_open).length;
      say(open ? open + ' exploit still works after the "fix". Claim rejected.' : 'Every exploit that worked before now fails. Fix proven, not asserted.'); });
  };

  const vulnIdx = R.gates.findIndex(g => g.gate === 'vuln.fixed');
  R.gates.forEach((g, k) => { if (k === vulnIdx) arena(); gateStep(k); });
  if (vulnIdx < 0) arena();

  add(1600, () => {
    const ok = R.decision === 'PROMOTED', s = $('stamp');
    s.textContent = R.decision; s.className = 'big-stamp ' + (ok ? 'ok' : 'bad'); void s.offsetWidth; s.classList.add('show');
    const ul = $('reasons'); ul.textContent = '';
    (ok ? ['Every required gate passed for this exact commit and image.'] : R.blocked).forEach(r => ul.appendChild(el('li', null, (ok ? '✓ ' : '✕ ') + r)));
    const seal = $('seal'); seal.textContent = '';
    seal.appendChild(document.createTextNode(R.signed ? 'Sealed: signed keyless by the pipeline (cosign), verified in a separate job. Content hash '
      : 'Offline rehearsal (unsigned; CI signs every pack). Content hash '));
    seal.appendChild(el('code', null, String(R.hash).slice(0, 16) + '…'));
    if (R.run.pipeline_url) { seal.appendChild(document.createTextNode(' · ')); const a = el('a', null, 'pipeline');
      a.href = R.run.pipeline_url; seal.appendChild(a); }
    say(ok ? 'Promoted. The change carries its own proof.' : 'Blocked. Nothing ships on an unproven claim.');
    stop();
  });
  return S;
}

/* ---- player ---- */
function step() { if (i >= steps.length) return stop(); const s = steps[i++]; s.fn();
  if (playing) timer = setTimeout(step, (reduce ? 200 : s.ms) / speed); }
function play() { if (i >= steps.length) build(); playing = true; $('play').textContent = '❚❚ Pause';
  $('play').setAttribute('aria-label', 'Pause'); if (!ticker) ticker = setInterval(tick, 60); step(); }
function stop() { playing = false; clearTimeout(timer); $('play').textContent = '▶ Play'; $('play').setAttribute('aria-label', 'Play');
  setTimeout(() => { if (!playing) { clearInterval(ticker); ticker = null; } }, 1500); }
$('play').onclick = () => playing ? stop() : play();
$('next').onclick = () => { stop(); if (i >= steps.length) build(); if (!ticker) { ticker = setInterval(tick, 60); }
  steps[i] && steps[i++].fn(); };
$('restart').onclick = () => { stop(); build(); play(); };
$('speed').onclick = () => { speed = speed === 1 ? 2 : speed === 2 ? 0.5 : 1; $('speed').textContent = speed + '×'; };
document.addEventListener('keydown', e => { if (e.code === 'Space' && e.target === document.body) { e.preventDefault(); $('play').click(); } });

const tabs = $('tabs');
RUNS.forEach((R, k) => { const t = el('button', 'tab', R.label + ' · ' + R.decision);
  t.setAttribute('role', 'tab'); t.setAttribute('aria-selected', k === run);
  t.onclick = () => { stop(); run = k; [...tabs.children].forEach((c, j) => c.setAttribute('aria-selected', j === k));
    build(); history.replaceState(null, '', '#' + k); };
  tabs.appendChild(t); });
if (RUNS.length < 2) tabs.classList.add('hidden');
const h = parseInt(location.hash.slice(1), 10); if (h >= 0 && h < RUNS.length) { run = h; tabs.children[h] && [...tabs.children].forEach((c, j) => c.setAttribute('aria-selected', j === h)); }
build();
if (new URLSearchParams(location.search).has('autoplay')) play();
})();
"""
