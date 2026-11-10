# Selectively vendored Bilibili read core

Source: https://github.com/liu-xindi/polylens-bilibili-mcp

Version 0.13.0, commit `35b9c852342a9e8b8d09db78548d42d62b6f9d2a`.
Copyright 2026 polylens-mcp contributors. Apache License, Version 2.0;
the complete original license is in `polylens/LICENSE`.

Tulpa includes only the models, errors and selected read/QR API modules.
Package `__init__.py` files were replaced with empty package markers to avoid
importing the upstream MCP server and its unrelated tools. Other vendored files
are unchanged. SHA256s and source paths are recorded in `manifest.json`.

Tulpa's `content.py` replaces HTTP transport with a fixed GET route allowlist,
domain/IP checks and bounded responses. It adapts pagination and credential
storage without exposing upstream MCP registrations or social-write endpoints.
Upstream's MCP version constraint is not introduced into Tulpa's environment.

Mechanism references (no source or prompts copied):

- Yueying 0.2.1, MIT, `e8fb72eabf9a2193b8d8cf5ce4e5c17de4edbcfe`:
  https://github.com/vsh5dvsch7-png/yueying — subtitles, local ASR and real frames.
- bilibili-notify 0.10.1, MIT, `49400e2b69b7c8f8af5b89ef8e460f0939cbbdfc`:
  https://github.com/Akokk0/bilibili-notify/tree/v0.10.1 — native page path and Ark unwrapping.

Tulpa does not bundle or patch SnowLuma in this module. Its upstream runtime and
separate licensing remain governed by `doc/THIRD_PARTY.md`.
