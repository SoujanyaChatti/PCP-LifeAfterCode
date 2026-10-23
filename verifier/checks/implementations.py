"""The deterministic checks. Each is pure: (claim, source, tol) -> EvidenceRecord.

A FAIL caused by an asserted number disagreeing with observed evidence is
tagged `claim_mismatch` — this is the "caught lie" the whole product exists to
surface. Missing/ambiguous evidence yields INCONCLUSIVE, which the gate treats
as FAIL for promotion (fail closed).
"""
from __future__ import annotations

from verifier.core import FAIL, INCONCLUSIVE, PASS, check, evidence

# --------------------------------------------------------------------------
# plan.ready
# --------------------------------------------------------------------------
@check("plan.has_acceptance_criteria")
def plan_has_acceptance_criteria(claim, source, tol):
    issue = source.issue(claim["subject"].get("issue_iid"))
    body = (issue.get("description") or "")
    ok = "acceptance criteria" in body.lower()
    return evidence(
        "plan.has_acceptance_criteria", claim,
        source="gitlab_api", query="GET issue description",
        raw_response=issue, observed={"has_section": ok},
        verdict=PASS if ok else (INCONCLUSIVE if not issue else FAIL),
        reason="acceptance criteria section present" if ok else "no acceptance criteria section",
    )


@check("plan.risk_label_set")
def plan_risk_label_set(claim, source, tol):
    issue = source.issue(claim["subject"].get("issue_iid"))
    labels = issue.get("labels", [])
    risk = [l for l in labels if str(l).startswith("risk::")]
    return evidence(
        "plan.risk_label_set", claim,
        source="gitlab_api", query="GET issue labels",
        raw_response=issue, observed={"risk_labels": risk},
        verdict=PASS if risk else (INCONCLUSIVE if not issue else FAIL),
        reason=f"risk label {risk[0]}" if risk else "no risk:: label set",
    )


# --------------------------------------------------------------------------
# code.ready
# --------------------------------------------------------------------------
@check("code.mr_exists")
def code_mr_exists(claim, source, tol):
    mr = source.mr(claim["subject"].get("mr_iid"))
    ok = bool(mr.get("iid"))
    return evidence(
        "code.mr_exists", claim, source="gitlab_api", query="GET mr",
        raw_response=mr, observed={"iid": mr.get("iid")},
        verdict=PASS if ok else INCONCLUSIVE,
        reason="MR exists" if ok else "MR not found",
    )


@check("code.targets_main")
def code_targets_main(claim, source, tol):
    mr = source.mr(claim["subject"].get("mr_iid"))
    target = mr.get("target_branch")
    ok = target == "main"
    return evidence(
        "code.targets_main", claim, source="gitlab_api", query="GET mr.target_branch",
        raw_response=mr, observed={"target_branch": target},
        verdict=PASS if ok else (INCONCLUSIVE if not mr else FAIL),
        reason=f"targets {target}",
    )


@check("code.head_sha_matches")
def code_head_sha_matches(claim, source, tol):
    mr = source.mr(claim["subject"].get("mr_iid"))
    head = (mr.get("sha") or "")
    claimed = claim["subject"].get("commit_sha", "")
    ok = bool(head) and head.startswith(claimed[:12]) or claimed.startswith(head[:12]) if head else False
    return evidence(
        "code.head_sha_matches", claim, source="gitlab_api", query="GET mr.sha",
        raw_response=mr, observed={"mr_sha": head, "claimed_sha": claimed},
        verdict=PASS if ok else (INCONCLUSIVE if not mr else FAIL),
        reason="MR head matches claim" if ok else "MR head SHA does not match claim",
        tags=[] if ok else ["claim_mismatch"],
    )


# --------------------------------------------------------------------------
# tests.pass
# --------------------------------------------------------------------------
@check("tests.junit_zero_failures")
def tests_junit_zero_failures(claim, source, tol):
    report = source.test_report(claim["subject"].get("pipeline_id"))
    if not report:
        return evidence(
            "tests.junit_zero_failures", claim, source="gitlab_api",
            query="GET pipeline test_report", raw_response=report,
            observed={}, verdict=INCONCLUSIVE, reason="no test report available",
        )
    failed = report.get("failed_count", report.get("failed"))
    total = report.get("total_count", report.get("total"))
    asserted = claim.get("asserted", {})
    a_failed = asserted.get("tests_failed")
    a_passed = asserted.get("tests_passed")
    observed = {"total": total, "failed": failed, "asserted_failed": a_failed, "asserted_passed": a_passed}

    if failed is None:
        return evidence("tests.junit_zero_failures", claim, source="gitlab_api",
                        query="GET pipeline test_report", raw_response=report,
                        observed=observed, verdict=INCONCLUSIVE, reason="report missing failed count")

    # Caught lie: agent asserts 0 failures but the report shows failures.
    if a_failed is not None and a_failed != failed:
        return evidence("tests.junit_zero_failures", claim, source="gitlab_api",
                        query="GET pipeline test_report", raw_response=report,
                        observed=observed, verdict=FAIL,
                        reason=f"claim asserts {a_failed} failures but report shows {failed}",
                        tags=["claim_mismatch"])
    if failed != 0:
        return evidence("tests.junit_zero_failures", claim, source="gitlab_api",
                        query="GET pipeline test_report", raw_response=report,
                        observed=observed, verdict=FAIL, reason=f"{failed} test failures")
    return evidence("tests.junit_zero_failures", claim, source="gitlab_api",
                    query="GET pipeline test_report", raw_response=report,
                    observed=observed, verdict=PASS, reason=f"0 failures in {total} tests")


@check("tests.count_not_lower_than_main")
def tests_count_not_lower_than_main(claim, source, tol):
    report = source.test_report(claim["subject"].get("pipeline_id"))
    total = report.get("total_count", report.get("total")) if report else None
    baseline = source.main_test_count()
    observed = {"total": total, "main_baseline": baseline}
    if total is None:
        return evidence("tests.count_not_lower_than_main", claim, source="gitlab_api",
                        query="test count vs main", raw_response=report, observed=observed,
                        verdict=INCONCLUSIVE, reason="no test count available")
    if baseline is None:
        # No baseline recorded yet (first run); accept but note it.
        return evidence("tests.count_not_lower_than_main", claim, source="gitlab_api",
                        query="test count vs main", raw_response=report, observed=observed,
                        verdict=PASS, reason="no main baseline; accepting current count")
    ok = total >= baseline
    return evidence("tests.count_not_lower_than_main", claim, source="gitlab_api",
                    query="test count vs main", raw_response=report, observed=observed,
                    verdict=PASS if ok else FAIL,
                    reason=f"{total} tests vs main baseline {baseline}")


@check("tests.coverage_within_tolerance")
def tests_coverage_within_tolerance(claim, source, tol):
    observed_cov = source.coverage(claim["subject"].get("pipeline_id"))
    asserted_cov = claim.get("asserted", {}).get("coverage_pct")
    floor = tol.get("coverage_floor", 70.0)
    band = tol.get("coverage_pct", 1.0)
    observed = {"coverage_pct": observed_cov, "asserted_pct": asserted_cov, "floor": floor}
    if observed_cov is None:
        return evidence("tests.coverage_within_tolerance", claim, source="gitlab_api",
                        query="pipeline coverage", raw_response={"coverage": observed_cov},
                        observed=observed, verdict=INCONCLUSIVE, reason="no coverage reported")
    if observed_cov < floor:
        return evidence("tests.coverage_within_tolerance", claim, source="gitlab_api",
                        query="pipeline coverage", raw_response={"coverage": observed_cov},
                        observed=observed, verdict=FAIL,
                        reason=f"coverage {observed_cov}% below floor {floor}%")
    if asserted_cov is not None and abs(asserted_cov - observed_cov) > band:
        return evidence("tests.coverage_within_tolerance", claim, source="gitlab_api",
                        query="pipeline coverage", raw_response={"coverage": observed_cov},
                        observed=observed, verdict=FAIL,
                        reason=f"asserted {asserted_cov}% vs observed {observed_cov}% exceeds ±{band}",
                        tags=["claim_mismatch"])
    return evidence("tests.coverage_within_tolerance", claim, source="gitlab_api",
                    query="pipeline coverage", raw_response={"coverage": observed_cov},
                    observed=observed, verdict=PASS, reason=f"coverage {observed_cov}% within tolerance")


# --------------------------------------------------------------------------
# pipeline.green
# --------------------------------------------------------------------------
@check("pipeline.latest_succeeded")
def pipeline_latest_succeeded(claim, source, tol):
    pl = source.pipeline(claim["subject"].get("pipeline_id"))
    status = pl.get("status")
    sha = pl.get("sha", "")
    claimed = claim["subject"].get("commit_sha", "")
    sha_ok = (not claimed) or (not sha) or sha.startswith(claimed[:12]) or claimed.startswith(sha[:12])
    observed = {"status": status, "pipeline_sha": sha}
    if not pl:
        return evidence("pipeline.latest_succeeded", claim, source="gitlab_api",
                        query="GET pipeline", raw_response=pl, observed=observed,
                        verdict=INCONCLUSIVE, reason="no pipeline data")
    if not sha_ok:
        return evidence("pipeline.latest_succeeded", claim, source="gitlab_api",
                        query="GET pipeline", raw_response=pl, observed=observed,
                        verdict=FAIL, reason="pipeline SHA differs from claim",
                        tags=["claim_mismatch"])
    ok = status == "success"
    return evidence("pipeline.latest_succeeded", claim, source="gitlab_api",
                    query="GET pipeline", raw_response=pl, observed=observed,
                    verdict=PASS if ok else FAIL, reason=f"pipeline status {status}")


@check("pipeline.no_allow_failure_added")
def pipeline_no_allow_failure_added(claim, source, tol):
    diff = source.mr_ci_diff(claim["subject"].get("mr_iid"))
    added_allow = any(
        line.startswith("+") and "allow_failure" in line and "true" in line
        for line in diff.splitlines()
    )
    return evidence("pipeline.no_allow_failure_added", claim, source="gitlab_api",
                    query="MR diff of .gitlab-ci.yml", raw_response={"diff": diff},
                    observed={"added_allow_failure": added_allow},
                    verdict=FAIL if added_allow else PASS,
                    reason="allow_failure:true added in this MR" if added_allow
                    else "no allow_failure weakening",
                    tags=["claim_mismatch"] if added_allow else [])


# --------------------------------------------------------------------------
# image.built
# --------------------------------------------------------------------------
@check("image.digest_exists")
def image_digest_exists(claim, source, tol):
    digest = claim["subject"].get("image_digest", "")
    img = source.registry_image(digest)
    ok = bool(img.get("digest"))
    return evidence("image.digest_exists", claim, source="container_registry",
                    query=f"HEAD image {digest}", raw_response=img,
                    observed={"digest": img.get("digest")},
                    verdict=PASS if ok else INCONCLUSIVE,
                    reason="digest present in registry" if ok else "digest not found")


@check("image.built_from_claimed_sha")
def image_built_from_claimed_sha(claim, source, tol):
    digest = claim["subject"].get("image_digest", "")
    img = source.registry_image(digest)
    label_sha = img.get("labels", {}).get("commit_sha", "")
    claimed = claim["subject"].get("commit_sha", "")
    ok = bool(label_sha) and (label_sha.startswith(claimed[:12]) or claimed.startswith(label_sha[:12]))
    return evidence("image.built_from_claimed_sha", claim, source="container_registry",
                    query=f"image labels for {digest}", raw_response=img,
                    observed={"label_sha": label_sha, "claimed_sha": claimed},
                    verdict=PASS if ok else (INCONCLUSIVE if not img else FAIL),
                    reason="image built from claimed SHA" if ok else "image SHA label mismatch",
                    tags=[] if ok else ["claim_mismatch"])


# --------------------------------------------------------------------------
# vuln.fixed
# --------------------------------------------------------------------------
@check("vuln.replay_now_fails")
def vuln_replay_now_fails(claim, source, tol):
    exploit_id = claim.get("asserted", {}).get("exploit_id", "")
    res = source.replay_result(exploit_id)
    if not res:
        return evidence("vuln.replay_now_fails", claim, source="exploit_replay",
                        query=f"replay {exploit_id}", raw_response=res, observed={},
                        verdict=INCONCLUSIVE, reason="no replay result for exploit")
    still_succeeds = res.get("succeeded", True)
    return evidence("vuln.replay_now_fails", claim, source="exploit_replay",
                    query=f"replay {exploit_id}", raw_response=res,
                    observed={"exploit_id": exploit_id, "still_succeeds": still_succeeds},
                    verdict=FAIL if still_succeeds else PASS,
                    reason="exploit still succeeds after patch" if still_succeeds
                    else "exploit now fails after patch",
                    tags=["claim_mismatch"] if still_succeeds else [])


@check("vuln.regression_test_present")
def vuln_regression_test_present(claim, source, tol):
    needed = claim.get("asserted", {}).get("regression_test", "")
    tests = source.regression_tests()
    ok = needed in tests if needed else len(tests) > 0
    return evidence("vuln.regression_test_present", claim, source="gitlab_api",
                    query="collected regression tests", raw_response={"tests": tests},
                    observed={"needed": needed, "present": ok},
                    verdict=PASS if ok else FAIL,
                    reason="regression test present and collected" if ok
                    else "no matching regression test found")


# --------------------------------------------------------------------------
# deploy.healthy
# --------------------------------------------------------------------------
@check("deploy.serves_claimed_digest")
def deploy_serves_claimed_digest(claim, source, tol):
    claimed = claim["subject"].get("image_digest", "")
    served = source.deploy_digest("production")
    ok = bool(served) and served == claimed
    return evidence("deploy.serves_claimed_digest", claim, source="cloud_run",
                    query="GET prod service digest", raw_response={"digest": served},
                    observed={"served": served, "claimed": claimed},
                    verdict=PASS if ok else (INCONCLUSIVE if not served else FAIL),
                    reason="prod serves claimed digest" if ok else "prod serves a different digest",
                    tags=[] if ok else ["claim_mismatch"])


@check("deploy.health_probes_pass")
def deploy_health_probes_pass(claim, source, tol):
    url = claim["subject"].get("staging_url") or ""
    n = int(tol.get("health_probes_required", 5))
    probes = source.health_probes(url, n)
    good = sum(1 for c in probes if c == 200)
    ok = good >= n and len(probes) >= n
    return evidence("deploy.health_probes_pass", claim, source="http_probe",
                    query=f"{n}x GET /healthz", raw_response={"probes": probes},
                    observed={"probes": probes, "good": good, "required": n},
                    verdict=PASS if ok else (INCONCLUSIVE if not probes else FAIL),
                    reason=f"{good}/{n} probes returned 200")


@check("deploy.error_rate_below_threshold")
def deploy_error_rate_below_threshold(claim, source, tol):
    rate = source.error_rate("production")
    thresh = tol.get("error_rate_max", 0.02)
    if rate is None:
        return evidence("deploy.error_rate_below_threshold", claim, source="cloud_monitoring",
                        query="error rate (5m)", raw_response={"error_rate": rate},
                        observed={"error_rate": None, "threshold": thresh},
                        verdict=INCONCLUSIVE, reason="no error-rate metric available")
    ok = rate <= thresh
    return evidence("deploy.error_rate_below_threshold", claim, source="cloud_monitoring",
                    query="error rate (5m)", raw_response={"error_rate": rate},
                    observed={"error_rate": rate, "threshold": thresh},
                    verdict=PASS if ok else FAIL,
                    reason=f"error rate {rate} vs threshold {thresh}")
