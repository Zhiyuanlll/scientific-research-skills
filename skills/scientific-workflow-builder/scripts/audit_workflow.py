#!/usr/bin/env python3
"""Read-only workflow-contract and run-record checks; Python standard library only."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


STATUSES = {"planned", "running", "complete", "partial", "failed", "stale"}
CHECK_STATES = {"passed", "failed", "pending"}
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class InputError(ValueError):
    pass


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON object key: " + repr(key))
        result[key] = value
    return result


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique_object)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


class Audit:
    def __init__(self):
        self.errors = []
        self.stages = {}
        self.stage_order = []
        self.run_errors = {}
        self.active_run = None

    def error(self, code, location, message):
        self.errors.append({"code": code, "location": location, "message": message})
        if self.active_run is not None:
            self.run_errors[self.active_run] = True

    def object(self, value, location):
        if not isinstance(value, dict):
            self.error("schema", location, "Expected an object.")
            return False
        return True

    def array(self, value, location):
        if not isinstance(value, list):
            self.error("schema", location, "Expected an array.")
            return []
        return value

    def strings(self, value, location):
        items = self.array(value, location)
        result = []
        for index, item in enumerate(items):
            if not nonempty(item):
                self.error("schema", f"{location}[{index}]", "Expected a nonempty string.")
            elif item in result:
                self.error("duplicate_id", location, "Duplicate identifier: " + item)
            else:
                result.append(item)
        return result

    def header(self, data, location, workflow_id=None):
        if not self.object(data, location):
            return False
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            self.error("schema", location + ".schema_version", "Expected schema_version 1.")
        if not nonempty(data.get("workflow_id")):
            self.error("schema", location + ".workflow_id", "Expected a nonempty workflow_id.")
        elif workflow_id is not None and data["workflow_id"] != workflow_id:
            self.error("workflow_mismatch", location + ".workflow_id", "Does not match the specification.")
        return True

    def declared_items(self, value, location, output=False):
        items = {}
        for index, item in enumerate(self.array(value, location)):
            item_loc = f"{location}[{index}]"
            if not self.object(item, item_loc):
                continue
            item_id = item.get("id")
            if not nonempty(item_id):
                self.error("schema", item_loc + ".id", "Expected a nonempty identifier.")
                continue
            if item_id in items:
                self.error("duplicate_id", item_loc + ".id", "Duplicate identifier: " + item_id)
            items[item_id] = item
            if not nonempty(item.get("description")):
                self.error("schema", item_loc + ".description", "Expected a nonempty description.")
            if output and ("units" not in item or not (item["units"] is None or nonempty(item["units"]))):
                self.error("schema", item_loc + ".units", "Expected null or a nonempty units string.")
        return items

    def specification(self, spec):
        if not self.header(spec, "spec"):
            return
        stage_list = self.array(spec.get("stages"), "spec.stages")
        if not stage_list:
            self.error("schema", "spec.stages", "At least one stage is required.")
        for index, stage in enumerate(stage_list):
            loc = f"spec.stages[{index}]"
            if not self.object(stage, loc):
                continue
            stage_id = stage.get("id")
            if not nonempty(stage_id):
                self.error("schema", loc + ".id", "Expected a nonempty stage identifier.")
                continue
            if stage_id in self.stages:
                self.error("duplicate_stage", loc + ".id", "Duplicate stage: " + stage_id)
                continue
            if not nonempty(stage.get("role")):
                self.error("schema", loc + ".role", "Expected a nonempty role.")
            self.stages[stage_id] = {
                "dependencies": self.strings(stage.get("depends_on"), loc + ".depends_on"),
                "acceptance": self.declared_items(stage.get("acceptance"), loc + ".acceptance"),
                "outputs": self.declared_items(stage.get("outputs"), loc + ".outputs", output=True),
            }
        for stage_id, stage in self.stages.items():
            for dep in stage["dependencies"]:
                if dep not in self.stages:
                    self.error("unknown_dependency", stage_id, "Unknown dependency: " + dep)
        # Kahn's algorithm avoids recursion limits for large specifications.
        remaining = {key: set(value["dependencies"]) for key, value in self.stages.items()}
        while remaining:
            ready = [key for key, deps in remaining.items() if not deps]
            if not ready:
                if all(dep in self.stages for deps in remaining.values() for dep in deps):
                    self.error("cycle", "spec.stages", "The stage dependency graph contains a cycle.")
                break
            self.stage_order.extend(ready)
            for key in ready:
                del remaining[key]
            for deps in remaining.values():
                deps.difference_update(ready)

    def affected(self, changed):
        affected = set()
        for stage_id in changed:
            if stage_id not in self.stages:
                self.error("unknown_changed_stage", "--changed-stage", "Unknown stage: " + stage_id)
            else:
                affected.add(stage_id)
        while True:
            expanded = affected | {
                key for key, stage in self.stages.items()
                if affected.intersection(stage["dependencies"])
            }
            if expanded == affected:
                break
            affected = expanded
        return [key for key in self.stages if key in affected]

    def file_claim(self, claim, location, root, required=True):
        if claim is None and not required:
            return
        if not self.object(claim, location):
            return
        path = claim.get("path")
        digest = claim.get("sha256")
        if not required and path is None and digest is None:
            return
        if not nonempty(path):
            self.error("schema", location + ".path", "Expected a nonempty file path.")
            return
        valid_digest = isinstance(digest, str) and bool(SHA256.fullmatch(digest))
        if not valid_digest:
            self.error("schema", location + ".sha256", "Expected a 64-character hexadecimal SHA256.")
        try:
            candidate = Path(path)
            resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                self.error("path_escape", location + ".path", "File resolves outside --root.")
                return
            if not resolved.is_file():
                self.error("missing_file", location + ".path", "Expected an existing regular file.")
                return
            if not valid_digest:
                return
            hasher = hashlib.sha256()
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest.lower():
                self.error("hash_mismatch", location + ".sha256", "SHA256 does not match the file.")
        except (OSError, ValueError, RuntimeError) as exc:
            self.error("file_access", location + ".path", str(exc))

    def run_records(self, data, workflow_id, root):
        if not self.header(data, "runs", workflow_id):
            return []
        records = {}
        for index, run in enumerate(self.array(data.get("runs"), "runs.runs")):
            loc = f"runs.runs[{index}]"
            if not self.object(run, loc):
                continue
            run_id = run.get("run_id")
            if not nonempty(run_id):
                self.error("schema", loc + ".run_id", "Expected a nonempty run identifier.")
                continue
            if run_id in records:
                self.error("duplicate_run", loc + ".run_id", "Duplicate run: " + run_id)
                self.run_errors[run_id] = True
                continue
            records[run_id] = run
            self.active_run = run_id
            stage_id = run.get("stage_id")
            if not nonempty(stage_id) or stage_id not in self.stages:
                self.error("unknown_stage", loc + ".stage_id", "Expected a declared stage identifier.")
            if not nonempty(run.get("case_id")):
                self.error("schema", loc + ".case_id", "Expected a nonempty case identifier.")
            if not isinstance(run.get("status"), str) or run["status"] not in STATUSES:
                self.error("schema", loc + ".status", "Unsupported run status.")
            # Retain the original record; parsed edges are stored separately.
            run_edges = self.strings(run.get("upstream_run_ids"), loc + ".upstream_run_ids")
            stage = self.stages.get(stage_id) if isinstance(stage_id, str) else None
            required = run.get("status") == "complete"
            if required or "provenance" in run:
                provenance = run.get("provenance")
                if provenance is None and not required:
                    pass
                elif self.object(provenance, loc + ".provenance"):
                    for key in ("config", "code"):
                        self.file_claim(provenance.get(key), loc + ".provenance." + key, root, required)
                    fingerprint = provenance.get("input_fingerprint")
                    if (required or fingerprint is not None) and not nonempty(fingerprint):
                        self.error("missing_provenance", loc + ".provenance.input_fingerprint", "Expected a nonempty input fingerprint.")
            checks = run.get("checks", {})
            if required or "checks" in run:
                if self.object(checks, loc + ".checks"):
                    for key, state in checks.items():
                        if stage is not None and key not in stage["acceptance"]:
                            self.error("unknown_check", loc + ".checks." + key, "Unknown acceptance criterion.")
                        if not isinstance(state, str) or state not in CHECK_STATES:
                            self.error("schema", loc + ".checks." + key, "Expected passed, failed or pending.")
                    if required and stage is not None:
                        for key in stage["acceptance"]:
                            if checks.get(key) != "passed":
                                self.error("acceptance_not_passed", loc + ".checks." + key, "Complete runs require every declared acceptance criterion to pass.")
            found = set()
            if required or "artifacts" in run:
                for art_index, artifact in enumerate(self.array(run.get("artifacts"), loc + ".artifacts")):
                    art_loc = f"{loc}.artifacts[{art_index}]"
                    if not self.object(artifact, art_loc):
                        continue
                    output_id = artifact.get("output_id")
                    if not nonempty(output_id):
                        self.error("schema", art_loc + ".output_id", "Expected a nonempty output identifier.")
                    else:
                        if stage is not None and output_id not in stage["outputs"]:
                            self.error("unknown_output", art_loc + ".output_id", "Unknown output identifier.")
                        if output_id in found:
                            self.error("duplicate_output", art_loc + ".output_id", "Duplicate output artifact.")
                        found.add(output_id)
                    self.file_claim(artifact, art_loc, root, required)
            if required and stage is not None:
                for key in sorted(stage["outputs"].keys() - found):
                    self.error("missing_output", loc + ".artifacts", "Missing declared output: " + key)
            run["_audit_edges"] = run_edges
            self.active_run = None

        for run_id, run in records.items():
            self.active_run = run_id
            stage = self.stages.get(run.get("stage_id")) if isinstance(run.get("stage_id"), str) else None
            covered = set()
            for upstream_id in run["_audit_edges"]:
                upstream = records.get(upstream_id)
                if upstream is None:
                    self.error("unknown_upstream", run_id, "Unknown upstream run: " + upstream_id)
                    continue
                upstream_stage = upstream.get("stage_id")
                if isinstance(upstream_stage, str):
                    covered.add(upstream_stage)
                if stage is not None and upstream_stage not in stage["dependencies"]:
                    self.error("unexpected_upstream", run_id, "Upstream run is not a direct stage dependency: " + upstream_id)
                if run.get("status") == "complete" and upstream.get("status") != "complete":
                    self.error("upstream_incomplete", run_id, "Complete run depends on a non-complete run: " + upstream_id)
            if stage is not None:
                for dep in sorted(set(stage["dependencies"]) - covered):
                    self.error("missing_upstream", run_id, "No upstream run covers dependency stage: " + dep)
            self.active_run = None

        # Propagate failures without assuming record order matches stage order.
        while True:
            previous = len(self.run_errors)
            for run_id, run in records.items():
                if run.get("status") == "complete" and run_id not in self.run_errors:
                    bad = [key for key in run["_audit_edges"] if key in self.run_errors]
                    if bad:
                        self.active_run = run_id
                        self.error("upstream_audit_failed", run_id, "An upstream record failed the audit: " + ", ".join(bad))
                        self.active_run = None
            if len(self.run_errors) == previous:
                break
        return [{
            "run_id": key,
            "status": run.get("status"),
            "audit": "failed" if key in self.run_errors else ("passed" if run.get("status") == "complete" else "incomplete"),
        } for key, run in records.items()]


def main(argv=None):
    parser = JsonParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="Workflow specification JSON")
    parser.add_argument("--runs", help="Run-record JSON; requires --root")
    parser.add_argument("--root", help="Directory containing all claimed evidence files")
    parser.add_argument("--changed-stage", action="append", default=[], help="Candidate change impact; repeatable")
    report = {
        "schema_version": 1,
        "workflow_id": None,
        "audit_mode": "design_only",
        "scope": {"kind": "workflow_design", "scientific_validation": "not_evaluated", "matrix_coverage": "not_evaluated"},
        "completion": "not_evaluated",
        "errors": [],
    }
    audit = Audit()
    try:
        args = parser.parse_args(argv)
        if args.runs and not args.root:
            raise InputError("--runs requires --root.")
        if args.root and not args.runs:
            raise InputError("--root requires --runs.")
        spec = read_json(args.spec)
        audit.specification(spec)
        if isinstance(spec, dict):
            report["workflow_id"] = spec.get("workflow_id")
        if args.changed_stage:
            report["affected_stages"] = audit.affected(args.changed_stage)
            report["impact_action"] = "candidate_invalidation_only; no run states modified"
        if args.runs:
            report["audit_mode"] = "run_records"
            report["completion"] = "incomplete"
            report["scope"]["kind"] = "provided_run_records"
            root = Path(args.root).resolve(strict=True)
            if not root.is_dir():
                raise InputError("--root must be an existing directory.")
            if not audit.errors:
                report["records"] = audit.run_records(read_json(args.runs), report["workflow_id"], root)
                report["scope"]["record_count"] = len(report["records"])
                if report["records"] and not audit.errors and all(item["audit"] == "passed" for item in report["records"]):
                    report["completion"] = "records_complete"
    except (InputError, OSError, ValueError, RuntimeError) as exc:
        audit.error("input_error", "input", str(exc))
    report["errors"] = audit.errors
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 1 if audit.errors or report["completion"] == "incomplete" else 0


if __name__ == "__main__":
    sys.exit(main())
