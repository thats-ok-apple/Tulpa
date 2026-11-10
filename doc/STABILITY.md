# Local ingestion and image search stability

## Runtime behavior

- Keep the account-bound native cursor. A changed WeChat boundary is diagnosed and replayed from a bounded batch; messages and the next cursor commit together through the existing Store transaction. Native identity conflicts quarantine that conversation instead of overwriting it or stopping healthy conversations.
- Unacknowledged native rows are retained in `pending_ids` and revisited after the main cursor. A missing pending row is removed only after a fresh source read. No temporary server identity is invented.
- Recovery metadata lives in the persisted checkpoint. Source table removal and repeated/conflicting recoveries remain visible as blocked issues. `/api/live-recovery/retry` accepts `platform` and an opaque `issue_id` from `/api/live-status`; it retries that issue without clearing unrelated cursors. Existing deletion tombstones continue to apply.
- Local direct MCP is primary on this computer. The plugin/tunnel remains configured for fallback. A bridge HTTP 429 is a connection-layer problem, not a WeChat ingestion status; do not repeatedly hammer the bridge.

## Images and OCR

- `media_retry_jobs` is a durable queue. Automatic retry examines at most four resolvable messages per pass, persists backoff, and keeps an explicit `window_expired` state after seven days. Missing native files are not downloaded. Manual scoped media refresh remains available.
- `image_ocr` and its FTS are separate from original message content. One local worker indexes available ordinary images from the last 30 days, plus newly imported eligible images. Stickers and animations are excluded by default. Cache identity includes the image digest and OCR engine version.
- Dependencies are pinned in `requirements-media.txt`. On this Mac, RapidOCR models are cached in `.cache/rapidocr`. ONNX telemetry is disabled. No image is sent to a cloud OCR endpoint by the indexer.
- `search_messages` defaults to both text and completed OCR. Optional `search_source` is `any`, `text`, or `image_ocr`. Results expose `image_text`, confidence, image index, thumbnail flag, and machine-source attribution; original `content` is unchanged. MCP connections without media permission cannot retrieve OCR text through search.
- Message deletion removes OCR entries/FTS and unreferenced OCR text cache. A deleted or changed message is checked again before an in-flight OCR result can commit.
- `read_image` returns pixels in MCP `content` image blocks. An orchestrator must forward these blocks to the model (Codex: `image(block)`), not only print `structuredContent`.

## Local maintenance API

All routes are loopback/same-origin, never model-authorized management tools:

- `GET /api/images/status`: worker queue counts and local engine status.
- `PUT /api/images/settings` with `{"enabled":false}`: pause OCR; `true` resumes it.
- `POST /api/images/backfill` with `start`, `end`, and optional `platform`: enqueue an explicit date range up to one year.
- `POST /api/images/{message_id}/index`: prioritize an existing message, including older images, without expanding any MCP grant.
- `GET /api/live-status`: existing fields plus last successful read, age/lag diagnostics, pending acknowledgements, recovery issues, and image queue counts. Age since the last imported message is not proof that new native messages exist.

The desktop status page now caches historical import coverage instead of opening every realtime source archive. Startup reads reconnect with bounded exponential backoff; a UI initialization error does not falsely indicate the native listener is stopped.

## Checks and rollback

Synthetic checks: `scripts/check_stability.py`, `check_live.py`, `check_mcp.py`, `check_qq_media_live.py`, `check_wechat_media_live.py`, `check_wechat_macos.py`, `check_wechat_no_images.py`, `check_chat_browser.py`, and `check_vision_mode.py`. MCP integration requires a temporary loopback listener.

Before deployment, make a SQLite online backup and preserve the current source/config diff. Rollback must pause new background work, preserve the current database and newly ingested messages, and restore only the relevant code/config snapshot. Never copy the old database over the live database. Retained checkpoints/tombstones and native-identity deduplication must govern any overlap replay. New schema is additive; do not drop derived tables during an emergency code rollback.
