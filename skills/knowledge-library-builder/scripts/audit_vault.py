#!/usr/bin/env python3
"""Read-only structural audit of a Markdown/Obsidian library. Never uses network."""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

try:
    import yaml  # optional; no dependency installation
except ImportError:
    yaml = None


def scalar(text):
    """Conservative fallback for common scalar/list properties, not a YAML parser."""
    text = text.strip()
    if text.startswith(("'", '"')):
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except ValueError:
            return [scalar(x) for x in next(csv.reader([text[1:-1]], skipinitialspace=True))]
    return text


def frontmatter(text):
    opening = re.match(r"\A---[ \t]*\n", text)
    if not opening:
        return {}, text, None
    start = opening.end()
    end = re.search(r"(?m)^(?:---|\.\.\.)[ \t]*$", text[start:])
    if not end:
        return {}, text, "unclosed_frontmatter"
    finish = start + end.start()
    raw = text[start:finish]
    body_start = start + end.end()
    if yaml:
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            return {}, text[body_start:], "invalid_yaml: " + str(exc).splitlines()[0]
        if not isinstance(data, dict):
            return {}, text[body_start:], "frontmatter_is_not_mapping"
        return data, text[body_start:], None
    data, current = {}, None
    for line in raw.splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
        if match:
            current, value = match.groups()
            data[current] = scalar(value) if value else []
        else:
            item = re.match(r"^[ \t]+-[ \t]+(.*)$", line)
            if item and current and isinstance(data[current], list):
                data[current].append(scalar(item.group(1)))
    return data, text[body_start:], None


def mask_code(text):
    """Keep offsets/line numbers while ignoring fenced and inline code examples."""
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            lines.append(re.sub(r"[^\n]", " ", line))
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not match.group(2).strip():
                fence = None
        elif match:
            fence = (match.group(1)[0], len(match.group(1)))
            lines.append(re.sub(r"[^\n]", " ", line))
        else:
            lines.append(line)
    masked = "".join(lines)
    return re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", lambda m: re.sub(r"[^\n]", " ", m.group()), masked, flags=re.S)


def normalize_heading(value):
    value = unquote(value).strip().lstrip("#").strip()
    value = re.sub(r"[ \t]+#+[ \t]*$", "", value)
    value = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), value)
    value = re.sub(r"[*_`~]", "", value)
    return re.sub(r"\s+", " ", value).casefold()


def markdown_destinations(text):
    """Common inline links with balanced destination parentheses and optional titles."""
    for match in re.finditer(r"!?\[[^\]\n]*\]\(", text):
        start, depth, cursor = match.end(), 1, match.end()
        angle = cursor < len(text) and text[cursor] == "<"
        in_angle = angle
        while cursor < len(text):
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if in_angle:
                if char == ">":
                    in_angle = False
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            if char == "\n" and not in_angle:
                break
            cursor += 1
        if depth:
            continue
        raw = text[start:cursor].strip()
        if raw.startswith("<") and ">" in raw:
            raw = raw[1:raw.index(">")]
        else:
            raw = re.sub(r'''\s+(?:"[^"\n]*"|'[^'\n]*')\s*$''', "", raw)
        yield match.start(), re.sub(r"\\([() ])", r"\1", raw)
    # Reference definitions are checked once, including unused definitions.
    for match in re.finditer(r"(?m)^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(<[^>]+>|\S+)", text):
        yield match.start(), match.group(1).strip("<>")


def audit(vault, excludes=(), strict=False):
    vault = Path(vault).resolve()
    findings, links, notes, files, canvases = [], [], {}, [], []

    def issue(kind, path, detail, line=None):
        item = {"kind": kind, "file": str(path), "detail": detail}
        if line is not None:
            item["line"] = line
        findings.append(item)

    def excluded(path):
        rel = path.relative_to(vault).as_posix()
        return any(fnmatch.fnmatch(rel, pattern) for pattern in excludes)

    for directory, dirs, names in os.walk(vault, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not excluded(Path(directory) / d))
        for name in sorted(names):
            path = Path(directory) / name
            if name.startswith(".") or excluded(path):
                continue
            files.append(path)
            if path.suffix.lower() == ".canvas":
                canvases.append(path)
            if path.suffix.lower() != ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            except (OSError, UnicodeError) as exc:
                issue("unreadable_note", path.relative_to(vault), str(exc))
                continue
            data, body, error = frontmatter(text)
            if error:
                issue("frontmatter_error", path.relative_to(vault), error)
            aliases = data.get("aliases", data.get("alias", []))
            if isinstance(aliases, str):
                aliases = [aliases]
            if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
                issue("property_type", path.relative_to(vault), "aliases must be text or a list of text")
                aliases = []
            if strict:
                for key in ("type", "status", "updated"):
                    if key not in data or not data[key]:
                        issue("missing_property", path.relative_to(vault), key)
                for key in ("type", "status"):
                    if key in data and not isinstance(data[key], str):
                        issue("property_type", path.relative_to(vault), key + " must be text")
                if "sources" in data and not isinstance(data["sources"], list):
                    issue("property_type", path.relative_to(vault), "sources must be a list")
                if "updated" in data:
                    try:
                        dt.date.fromisoformat(str(data["updated"]))
                    except ValueError:
                        issue("property_type", path.relative_to(vault), "updated should be a valid YYYY-MM-DD date")
            masked_body = mask_code(body)
            headings = {normalize_heading(m.group(1)) for m in re.finditer(r"(?m)^ {0,3}#{1,6}[ \t]+(.+)$", masked_body)}
            # Common setext headings.
            headings.update(normalize_heading(m.group(1)) for m in re.finditer(r"(?m)^([^\n]+)\n {0,3}(?:={2,}|-{2,})[ \t]*$", masked_body))
            blocks = set(re.findall(r"(?m)(?:^|\s)\^([\w-]+)[ \t]*$", masked_body))
            notes[path] = {"metadata": data, "aliases": aliases, "headings": headings, "blocks": blocks, "body": body, "text": text}

    by_name = {}
    for path in files:
        for name in {path.name, path.stem}:
            by_name.setdefault(name, set()).add(path)
    by_alias = {}
    for path, note in notes.items():
        for alias in note["aliases"]:
            by_alias.setdefault(alias, set()).add(path)

    def wiki_candidates(target, current):
        target = unquote(target).replace("\\", "/")
        if not target:
            return {current}
        if "/" in target:
            rel = target.lstrip("/")
            paths = {vault / rel, current.parent / target}
            if not Path(target).suffix:
                paths |= {Path(str(path) + ".md") for path in list(paths)}
            return {path.resolve() for path in paths if path.is_file()}
        exact = by_name.get(target, set())
        # Prefer a unique file in this note's folder before global basename matches.
        local = {path for path in exact if path.parent == current.parent}
        return local or exact or by_alias.get(target, set())

    def anchor_check(path, anchor, owner, line):
        if not anchor or path not in notes:
            return
        if anchor.startswith("^"):
            valid = anchor[1:] in notes[path]["blocks"]
        else:
            valid = normalize_heading(anchor) in notes[path]["headings"]
        if not valid:
            issue("missing_anchor", owner, str(path.relative_to(vault)) + "#" + anchor, line)

    def markdown_check(dest, path, owner, line):
        try:
            parsed = urlsplit(dest)
        except ValueError:
            issue("malformed_link", owner, dest, line)
            return
        scheme = parsed.scheme.lower()
        if scheme and scheme != "file":
            state = "network_unchecked" if scheme in {"http", "https"} else "scheme_unchecked"
            links.append({"file": str(owner), "line": line, "target": dest, "state": state})
            return
        if scheme == "file" and parsed.netloc not in {"", "localhost"}:
            links.append({"file": str(owner), "line": line, "target": dest, "state": "remote_file_unchecked"})
            return
        target = unquote(parsed.path)
        anchor = unquote(parsed.fragment)
        candidate = path if not target else Path(target) if target.startswith("/") else path.parent / target
        candidate = candidate.resolve()
        if not candidate.exists():
            state = "missing"
            issue("missing_local_link", owner, dest, line)
        elif not os.access(candidate, os.R_OK):
            state = "unreadable"
            issue("unreadable_source", owner, dest, line)
        else:
            state = "available_directory" if candidate.is_dir() else "available_file"
            anchor_check(candidate, anchor, owner, line)
        links.append({"file": str(owner), "line": line, "target": dest, "state": state})

    for path, note in notes.items():
        owner = path.relative_to(vault)
        # Mask frontmatter but preserve its lines for useful source positions.
        full = note["text"]
        _, body, _ = frontmatter(full)
        prefix = full[:len(full) - len(body)]
        masked = re.sub(r"[^\n]", " ", prefix) + mask_code(body)
        for match in re.finditer(r"(?<!!)!?\[\[([^\]\n]+)\]\]", masked):
            target = match.group(1).split("|", 1)[0].strip()
            name, _, anchor = unquote(target).partition("#")
            candidates = wiki_candidates(name, path)
            line = masked.count("\n", 0, match.start()) + 1
            if not candidates:
                issue("missing_wikilink", owner, target, line)
            elif len(candidates) > 1:
                issue("ambiguous_wikilink", owner, target, line)
            else:
                anchor_check(next(iter(candidates)), anchor, owner, line)
        # Remove wiki constructs so link scanner does not reinterpret them.
        masked = re.sub(r"!?\[\[[^\]\n]+\]\]", lambda m: " " * len(m.group()), masked)
        for offset, dest in markdown_destinations(masked):
            markdown_check(dest, path, owner, masked.count("\n", 0, offset) + 1)

    for path in canvases:
        owner = path.relative_to(vault)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
                raise ValueError("Canvas needs node and edge lists")
            ids = [node.get("id") for node in data["nodes"] if isinstance(node, dict)]
            if len(ids) != len(data["nodes"]) or None in ids or len(set(ids)) != len(ids):
                issue("canvas_node_ids", owner, "missing or duplicate node ids")
            for edge in data["edges"]:
                if not isinstance(edge, dict) or edge.get("fromNode") not in ids or edge.get("toNode") not in ids:
                    issue("canvas_edge_target", owner, str(edge))
            for node in data["nodes"]:
                if isinstance(node, dict) and node.get("type") == "file":
                    target = node.get("file")
                    if not isinstance(target, str) or not (vault / unquote(target)).is_file():
                        issue("canvas_missing_file", owner, str(target))
                    else:
                        anchor_check((vault / unquote(target)).resolve(), str(node.get("subpath", "")).lstrip("#"), owner, None)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            issue("invalid_canvas", owner, str(exc))

    groups = {}
    for path, note in notes.items():
        body = re.sub(r"\A\s*#[ \t]+[^\n]+\n", "", note["body"])
        normalized = re.sub(r"\s+", " ", body).strip()
        if len(normalized) >= 80:
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            groups.setdefault(digest, []).append(str(path.relative_to(vault)))
    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    return {
        "vault": str(vault), "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary": {"markdown_notes": len(notes), "canvas_files": len(canvases), "findings": len(findings), "exact_duplicate_groups": len(duplicate_groups)},
        "yaml_parser": "PyYAML safe_load" if yaml else "conservative fallback; full YAML validation not performed",
        "limitations": ["No scientific or semantic correctness check", "No network requests; external URLs are unverified", "Complex/plugin-specific Markdown and YAML need manual review", "Exact duplicate groups are candidates; no files are changed"],
        "findings": findings, "local_and_external_links": links, "exact_duplicate_groups": duplicate_groups,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True, help="JSON report path outside vault")
    parser.add_argument("--exclude", action="append", default=[], help="vault-relative glob; repeatable")
    parser.add_argument("--strict-properties", action="store_true", help="require type/status/updated; only for libraries using that schema")
    args = parser.parse_args(argv)
    vault, report_path = args.vault.resolve(), args.report.resolve()
    if not vault.is_dir():
        parser.error("--vault must be an existing directory")
    if report_path == vault or vault in report_path.parents:
        parser.error("--report must be outside vault to preserve read-only auditing")
    report = audit(vault, args.exclude, args.strict_properties)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
