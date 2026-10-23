"""Trust ledger: hash chain, tamper detection, earned-autonomy tiers, policy."""
import json

from verifier import ledger as L

CFG = {"window_runs": 20, "min_verified": 2, "probation_lookback": 5, "suspend_lies": 3}


def _entry(decision="PROMOTED", **agents):
    """agents: name=[(verdict, lie), ...]"""
    claims = [{"agent": a, "type": "x", "verdict": v, "lie": lie}
              for a, outcomes in agents.items() for v, lie in outcomes]
    return {"run": {"commit_sha": "c"}, "decision": decision, "claims": claims}


def _ledger(*entries):
    led = {"entries": []}
    for e in entries:
        L.append(led, e)
    return led


def test_chain_links_and_verifies():
    led = _ledger(_entry(builder=[("PASS", False)]), _entry(builder=[("PASS", False)]))
    assert led["entries"][0]["prev_hash"] == L.GENESIS
    assert led["entries"][1]["prev_hash"] == led["entries"][0]["hash"]
    assert L.verify_chain(led) == (True, None)


def test_editing_history_breaks_the_chain():
    led = _ledger(_entry(builder=[("FAIL", True)]), _entry(builder=[("PASS", False)]))
    led["entries"][0]["claims"][0]["lie"] = False  # erase the caught lie
    ok, at = L.verify_chain(led)
    assert not ok and at == 0


def test_deleting_an_entry_breaks_the_chain():
    led = _ledger(*[_entry(builder=[("PASS", False)]) for _ in range(3)])
    del led["entries"][1]
    assert L.verify_chain(led) == (False, 1)


def test_new_agent_starts_on_probation_then_earns_autonomy():
    led = _ledger(_entry(builder=[("PASS", False)]))
    assert L.summarize(led, CFG)["agents"]["builder"]["tier"] == "probation"
    L.append(led, _entry(builder=[("PASS", False)]))
    assert L.summarize(led, CFG)["agents"]["builder"]["tier"] == "autonomous"


def test_caught_lie_means_probation_and_it_decays():
    led = _ledger(*[_entry(builder=[("PASS", False)]) for _ in range(3)],
                  _entry("BLOCKED", builder=[("FAIL", True)]))
    s = L.summarize(led, CFG)["agents"]["builder"]
    assert s["tier"] == "probation" and s["lies"] == 1
    for _ in range(CFG["probation_lookback"]):
        L.append(led, _entry(builder=[("PASS", False)]))
    assert L.summarize(led, CFG)["agents"]["builder"]["tier"] == "autonomous"


def test_repeated_lies_suspend():
    led = _ledger(*[_entry("BLOCKED", blueteam=[("FAIL", True)]) for _ in range(3)])
    assert L.summarize(led, CFG)["agents"]["blueteam"]["tier"] == "suspended"


def test_policy_holds_probation_unless_human_approves_and_never_suspended():
    led = _ledger(_entry(builder=[("PASS", False)]),
                  *[_entry("BLOCKED", blueteam=[("FAIL", True)]) for _ in range(3)])
    s = L.summarize(led, CFG)
    ok, reasons = L.policy(s, ["builder"], human_approved=False)
    assert not ok and "probation" in reasons[0]
    assert L.policy(s, ["builder"], human_approved=True)[0]
    ok, reasons = L.policy(s, ["blueteam"], human_approved=True)
    assert not ok and "suspended" in reasons[0]


def test_policy_refuses_a_tampered_ledger():
    led = _ledger(*[_entry(builder=[("PASS", False)]) for _ in range(3)])
    led["entries"][0]["decision"] = "BLOCKED"
    ok, reasons = L.policy(L.summarize(led, CFG), ["builder"], human_approved=True)
    assert not ok and "chain broken" in reasons[0]


def test_cli_append_summary_policy_verify(tmp_path, monkeypatch):
    ledger, pack = tmp_path / "ledger.json", tmp_path / "pack.json"
    pack.write_text(json.dumps({"decision": "PROMOTED", "content_sha256": "abc",
                                "run": {"commit_sha": "c1"},
                                "judged_claims": [{"agent": "builder", "type": "tests.pass",
                                                   "verdict": "PASS", "lie": False}]}))
    for _ in range(2):
        assert L.main(["append", "--ledger", str(ledger), "--pack", str(pack)]) == 0
    assert L.main(["verify", "--ledger", str(ledger)]) == 0
    out = tmp_path / "trust.json"
    assert L.main(["summary", "--ledger", str(ledger), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["agents"]["builder"]["verified"] == 2
    monkeypatch.delenv("PCP_HUMAN_APPROVED", raising=False)
    assert L.main(["policy", "--ledger", str(ledger), "--agents", "builder"]) == 0
    assert L.main(["policy", "--ledger", str(ledger), "--agents", "builder,blueteam"]) == 1
    data = json.loads(ledger.read_text())
    data["entries"][0]["pack_sha256"] = "forged"
    ledger.write_text(json.dumps(data))
    assert L.main(["verify", "--ledger", str(ledger)]) == 1


def test_registry_pull_push(monkeypatch, tmp_path):
    import httpx

    store = {}

    def get(url, headers, timeout, follow_redirects):
        assert headers == {"JOB-TOKEN": "tok"}
        if url not in store:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, content=store[url], request=httpx.Request("GET", url))

    def put(url, content, headers, timeout):
        store[url] = content
        return httpx.Response(201, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "put", put)
    monkeypatch.setenv("CI_API_V4_URL", "https://gl/api/v4")
    monkeypatch.setenv("CI_PROJECT_ID", "5")
    monkeypatch.setenv("CI_JOB_TOKEN", "tok")
    path = tmp_path / "ledger.json"
    assert L.main(["pull", "--ledger", str(path)]) == 0 and not path.exists()
    path.write_text('{"entries": []}')
    assert L.main(["push", "--ledger", str(path)]) == 0
    path.unlink()
    assert L.main(["pull", "--ledger", str(path)]) == 0
    assert json.loads(path.read_text()) == {"entries": []}
