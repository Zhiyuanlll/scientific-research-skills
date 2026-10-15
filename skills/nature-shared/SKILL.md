---
name: nature-shared
description: Internal shared-reference support package for installed nature-writing, nature-polishing, nature-reader, and nature-paper2ppt skills. Do not invoke it as a standalone user workflow. Load only the specific core or journal-format file requested by another Nature skill.
---

<!-- Redistribution/adaptation notice: integrated local snapshot, 2026-10-15. Upstream: Yuan1z0825/nature-skills. See ../../THIRD_PARTY_NOTICES.md and PACKAGE_MANIFEST.json for origins, licenses and recorded changes. -->

# Nature Shared References

Use this package only as a dependency of another installed Nature skill.

- Load the exact referenced file; do not preload the whole package.
- Treat `core/` and `journal-formats/` as shared definitions, not standalone workflows.
- Return to the requesting skill for task logic, output format, and final QA.
