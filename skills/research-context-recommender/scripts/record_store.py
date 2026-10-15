#!/usr/bin/env python3
"""Small append-only research record store; no search or feedback interpretation.

Usage: record_store.py --store records.json init|context
       record_store.py --store records.json append --input events.json

Sources are objects with a nonempty ``uri``. Aliases and work_family_id are
explicit, already verified identity declarations supplied by the caller. This
tool never infers identity from titles or treats missing feedback as negative.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile


class StoreError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise StoreError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(value, location):
    require(isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})",
        value), location + " must be an ISO timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StoreError(location + " is not a valid timestamp") from exc
    return result.astimezone(timezone.utc)


def sources(value, location, allow_empty=True):
    require(isinstance(value, list), location + " must be a list")
    require(allow_empty or value, location + " must contain evidence sources")
    for index, source in enumerate(value):
        require(isinstance(source, dict) and nonempty(source.get("uri")),
                f"{location}[{index}] must be an object with a nonempty uri")


def text_or_list(value):
    return nonempty(value) or (isinstance(value, list)
                              and all(nonempty(item) for item in value))


def validate_event(event):
    require(isinstance(event, dict), "event must be an object")
    require(set(event) == {"id", "kind", "recorded_at", "payload"},
            "event requires exactly id, kind, recorded_at, payload")
    require(nonempty(event["id"]), "event.id must be a nonempty string")
    timestamp(event["recorded_at"], "event.recorded_at")
    payload = event["payload"]
    require(isinstance(payload, dict), "event.payload must be an object")
    kind = event["kind"]
    if kind == "state":
        timestamp(payload.get("as_of"), "state.as_of")
        require(isinstance(payload.get("projects"), list), "state.projects must be a list")
        sources(payload.get("sources"), "state.sources")
    elif kind == "recommendation":
        require(nonempty(payload.get("run_id")), "recommendation.run_id is required")
        require(payload.get("status") in ("complete", "partial", "failed"),
                "recommendation.status must be complete, partial, or failed")
        require(isinstance(payload.get("report_markdown"), str),
                "recommendation.report_markdown must be a string")
        require(isinstance(payload.get("papers"), list), "recommendation.papers must be a list")
        for paper in payload["papers"]:
            require(isinstance(paper, dict), "paper must be an object")
            for key in ("paper_id", "title", "url", "question_id"):
                require(nonempty(paper.get(key)), f"paper.{key} must be a nonempty string")
            require(paper.get("evidence_level") in ("metadata", "abstract", "fulltext"),
                    "paper.evidence_level must be metadata, abstract, or fulltext")
            require(text_or_list(paper.get("reason")) and bool(paper["reason"]),
                    "paper.reason must be nonempty text or a nonempty list of text")
            require(text_or_list(paper.get("unverified")),
                    "paper.unverified must be text or a list of text (possibly empty)")
            sources(paper.get("sources"), "paper.sources", allow_empty=False)
            if "aliases" in paper:
                require(isinstance(paper["aliases"], list)
                        and all(nonempty(alias) for alias in paper["aliases"]),
                        "paper.aliases must be a list of verified identifier strings")
            if "work_family_id" in paper:
                require(nonempty(paper["work_family_id"]),
                        "paper.work_family_id must be a nonempty string")
    elif kind == "feedback":
        require(nonempty(payload.get("text")), "feedback.text must preserve nonempty original text")
        source = payload.get("source")
        require(isinstance(source, dict) and nonempty(source.get("uri"))
                and nonempty(source.get("message_id")),
                "feedback.source requires uri and message_id")
        scope = payload.get("scope")
        require(isinstance(scope, dict)
                and scope.get("kind") in ("paper", "question", "project", "global"),
                "feedback.scope.kind must be paper, question, project, or global")
        require(scope["kind"] == "global" or nonempty(scope.get("id")),
                "non-global feedback.scope requires a nonempty id")
        require(isinstance(payload.get("interpretation"), dict),
                "feedback.interpretation must be an object")
    else:
        raise StoreError("event.kind must be state, recommendation, or feedback")


def validate_store(data):
    require(isinstance(data, dict) and set(data) == {"schema_version", "events"},
            "store requires exactly schema_version and events")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1,
            "unsupported store schema_version")
    require(isinstance(data["events"], list), "store.events must be a list")
    seen = set()
    for event in data["events"]:
        validate_event(event)
        require(event["id"] not in seen, "duplicate event id in existing store: " + event["id"])
        seen.add(event["id"])
    return data


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=reject_duplicate_keys,
                             parse_constant=lambda token: (_ for _ in ()).throw(
                                 StoreError("invalid JSON constant: " + token)))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc


@contextmanager
def store_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def init_store(path):
    path = Path(path)
    with store_lock(path):
        if path.exists():
            validate_store(read_json(path))
            return {"created": False, "store": str(path)}
        atomic_write(path, {"schema_version": 1, "events": []})
    return {"created": True, "store": str(path)}


def append_events(path, incoming):
    path = Path(path)
    batch = incoming if isinstance(incoming, list) else [incoming]
    for event in batch:
        validate_event(event)
    with store_lock(path):
        data = validate_store(read_json(path))
        by_id = {event["id"]: event for event in data["events"]}
        added = 0
        for event in batch:
            previous = by_id.get(event["id"])
            if previous is not None:
                # JSON booleans and numbers must not compare equal as in Python
                # (True == 1); object key order is immaterial to event identity.
                require(json.dumps(previous, sort_keys=True, allow_nan=False)
                        == json.dumps(event, sort_keys=True, allow_nan=False),
                        "conflicting event id: " + event["id"])
                continue
            by_id[event["id"]] = event
            data["events"].append(event)
            added += 1
        if added:
            atomic_write(path, data)
    return {"added": added, "unchanged": len(batch) - added}


def context(path):
    path = Path(path)
    # Writers atomically replace the file; a reader sees one complete snapshot.
    # No read lock is needed, so this command creates no directories or files.
    data = validate_store(read_json(path))
    ordered = sorted(enumerate(data["events"]),
                     key=lambda pair: (timestamp(pair[1]["recorded_at"], "recorded_at"), pair[0]))
    events = [event for _, event in ordered]
    positions = {event["id"]: index for index, event in enumerate(events)}
    states = [event for event in events if event["kind"] == "state"]
    latest_state = max(states, key=lambda event: (
        timestamp(event["payload"]["as_of"], "state.as_of"),
        positions[event["id"]])) if states else None
    runs = [event for event in events if event["kind"] == "recommendation"]

    # Identity groups are joined only by explicit identifiers or work families.
    groups = []
    for event in runs:
        for paper in event["payload"]["papers"]:
            identifiers = {paper["paper_id"], *paper.get("aliases", [])}
            families = {paper["work_family_id"]} if paper.get("work_family_id") else set()
            matching = [group for group in groups if group["ids"] & identifiers
                        or group["families"] & families]
            if matching:
                group = matching[0]
                for other in matching[1:]:
                    group["ids"].update(other["ids"])
                    group["families"].update(other["families"])
                    group["entries"].extend(other["entries"])
                    groups.remove(other)
            else:
                group = {"ids": set(), "families": set(), "entries": []}
                groups.append(group)
            group["ids"].update(identifiers)
            group["families"].update(families)
            group["entries"].append((event, paper))

    known_papers = []
    for group in groups:
        entries = sorted(group["entries"], key=lambda entry: positions[entry[0]["id"]])
        first_event, first_paper = entries[0]
        last_event, last_paper = entries[-1]
        summary = {"paper_id": first_paper["paper_id"],
                   "aliases": sorted(group["ids"] - {first_paper["paper_id"]}),
                   "first_recommended_at": first_event["recorded_at"],
                   "latest_run_id": last_event["payload"]["run_id"],
                   "question_id": last_paper["question_id"], "title": last_paper["title"]}
        if group["families"]:
            summary["work_family_id"] = last_paper.get("work_family_id") or sorted(group["families"])[0]
            if len(group["families"]) > 1:
                summary["work_family_ids"] = sorted(group["families"])
        known_papers.append(summary)
    known_papers.sort(key=lambda paper: (timestamp(paper["first_recommended_at"], "first_recommended_at"),
                                         paper["paper_id"]))
    latest_runs = {event["payload"]["run_id"]: event for event in runs}
    recent_runs = sorted(latest_runs.values(), key=lambda event: positions[event["id"]])[-12:]
    return {"schema_version": 1, "latest_state": latest_state,
            "feedback": [event for event in events if event["kind"] == "feedback"],
            "known_papers": known_papers,
            "recent_runs": [{"event_id": event["id"], "recorded_at": event["recorded_at"],
                             "run_id": event["payload"]["run_id"],
                             "status": event["payload"]["status"],
                             "paper_count": len(event["payload"]["papers"])}
                            for event in recent_runs]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("context")
    append = commands.add_parser("append")
    append.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_store(args.store)
        elif args.command == "append":
            result = append_events(args.store, read_json(args.input))
        else:
            result = context(args.store)
    except (StoreError, OSError, TypeError) as exc:
        print("record_store: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
