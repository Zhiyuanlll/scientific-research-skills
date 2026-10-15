#!/usr/bin/env python3
"""Isolated behavioral checks; creates fixtures only in a temporary directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import audit_vault


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="library-builder-test-")
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        (self.vault / "sub").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, content):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def snapshot(self):
        return {str(p.relative_to(self.vault)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.vault.rglob("*") if p.is_file()}

    def test_resolution_code_examples_sources_and_read_only(self):
        self.put("sub/Target.md", "---\naliases:\n  - Other name\n---\n# Target\n## A heading\nEvidence paragraph. ^anchor\n")
        self.put("a (one).md", "# Attachment-like note\n")
        self.put("a space.pdf", "fixture")
        self.put("Home.md", """# Home
[[Target.md#A%20heading]] [[Other name]] [[sub/Target#^anchor]]
[[sub/Target.md|Display text]] ![[a space.pdf]]
[local](a%20space.pdf) [parentheses](a%20(one).md)
[angle](<a (one).md>) [external](https://example.invalid/test)
[missing](absent.pdf) [[Missing note]] [[Target#absent heading]]
`[[Fake inline]]`
```markdown
[[Fake fenced]] [fake](absent-code-file.pdf)
```
""")
        before = self.snapshot()
        report = audit_vault.audit(self.vault)
        self.assertEqual(before, self.snapshot())
        kinds = [entry["kind"] for entry in report["findings"]]
        self.assertCountEqual(kinds, ["missing_local_link", "missing_wikilink", "missing_anchor"])
        targets = {entry["target"]: entry["state"] for entry in report["local_and_external_links"]}
        self.assertEqual(targets["a%20space.pdf"], "available_file")
        self.assertEqual(targets["a%20(one).md"], "available_file")
        self.assertEqual(targets["a (one).md"], "available_file")
        self.assertEqual(targets["https://example.invalid/test"], "network_unchecked")

    def test_ambiguous_alias_duplicates_canvas_and_properties(self):
        content = "A substantive paragraph whose content is deliberately the same in two notes, with enough detail to trigger the exact content duplicate candidate check.\n"
        self.put("One.md", "---\naliases: [same]\n---\n# One\n" + content)
        self.put("sub/Two.md", "---\naliases: [same]\n---\n# Two\n" + content)
        self.put("Home.md", "---\ntype: index\nstatus: reviewed\nupdated: 2026-01-01\nsources: wrong-scalar\n---\n# Home\n[[same]]\n")
        self.put("Graph.canvas", json.dumps({"nodes": [{"id": "a", "type": "file", "file": "One.md"}], "edges": [{"id": "e", "fromNode": "a", "toNode": "missing"}]}))
        report = audit_vault.audit(self.vault, strict=True)
        kinds = {entry["kind"] for entry in report["findings"]}
        self.assertTrue({"ambiguous_wikilink", "canvas_edge_target", "missing_property", "property_type"}.issubset(kinds))
        self.assertEqual(report["exact_duplicate_groups"], [["One.md", "sub/Two.md"]])
        self.assertFalse(any(entry["kind"] == "canvas_missing_file" for entry in report["findings"]))

    def test_fallback_and_unclosed_frontmatter(self):
        with patch.object(audit_vault, "yaml", None):
            data, body, error = audit_vault.frontmatter("---  \naliases:\n  - Plain alias\nsources: [S-001, S-002]\n---\n# Body\n")
            self.assertIsNone(error)
            self.assertEqual(data["aliases"], ["Plain alias"])
            self.assertEqual(data["sources"], ["S-001", "S-002"])
            self.assertIn("# Body", body)
            _, _, error = audit_vault.frontmatter("---\naliases: [a]\n# Unclosed\n")
            self.assertEqual(error, "unclosed_frontmatter")

    def test_cli_report_outside_vault_and_exclusions(self):
        self.put("Home.md", "# Home\n")
        self.put("archive/Old.md", "# Old\n[[not available]]\n")
        script = Path(audit_vault.__file__)
        before = self.snapshot()
        report_path = self.root / "audit.json"
        result = subprocess.run([sys.executable, str(script), "--vault", str(self.vault), "--report", str(report_path), "--exclude", "archive/**"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(report_path.read_text())["summary"]["findings"], 0)
        bad = self.vault / "should-not-exist.json"
        result = subprocess.run([sys.executable, str(script), "--vault", str(self.vault), "--report", str(bad)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(bad.exists())
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
