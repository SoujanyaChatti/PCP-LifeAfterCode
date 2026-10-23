"""Convert a JUnit report.xml + coverage.xml into the fixture layout the
verifier's FixtureSource reads, so verify:tests runs on the REAL test output
(authoritative evidence) rather than anything an agent hand-wrote."""
from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET


def parse_junit(path: str) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    total = failed = 0
    for s in suites:
        total += int(s.get("tests", 0))
        failed += int(s.get("failures", 0)) + int(s.get("errors", 0))
    return {"total_count": total, "failed_count": failed}


def parse_coverage(path: str) -> float | None:
    if not path or not os.path.exists(path):
        return None
    root = ET.parse(path).getroot()
    rate = root.get("line-rate")
    return round(float(rate) * 100, 2) if rate is not None else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--coverage", default=None)
    p.add_argument("--baseline", default=None, help="optional main test-count baseline")
    p.add_argument("--commit", default=os.environ.get("CI_COMMIT_SHA", ""),
                   help="commit the report was produced for (binds the evidence)")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    report = parse_junit(args.report)
    if args.commit:
        report["commit_sha"] = args.commit
    with open(os.path.join(args.out, "test_report.json"), "w") as f:
        json.dump(report, f)
    cov = parse_coverage(args.coverage)
    with open(os.path.join(args.out, "coverage.json"), "w") as f:
        json.dump({"coverage_pct": cov}, f)
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline) as f:
            baseline = json.load(f)
        with open(os.path.join(args.out, "main_baseline.json"), "w") as f:
            json.dump(baseline, f)
    print(f"fixture written to {args.out}: {report}, coverage={cov}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
