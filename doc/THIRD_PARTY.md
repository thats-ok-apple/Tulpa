# Third-party components

For the Linux source package, see [Linux third-party components](LINUX_THIRD_PARTY.md). The desktop distribution notices below remain applicable to its bundled components.

The MIT license at the repository root applies to Tulpa's own code. It does not
relicense the dependencies, model weights, browser runtime or external services.
The portable ZIP is an aggregate distribution. Original license and copyright
files are retained in `licenses/`, `runtime/`, Python package `.dist-info/`
directories and the corresponding component directories. Exact package versions
and the source commit are recorded in `build-manifest.json`.

| Component | License / source |
| --- | --- |
| CPython 3.13.9 | [PSF license](https://www.python.org/downloads/release/python-3139/); `runtime/LICENSE.txt` |
| SQLite 3.53.2 / 3.53.4 | [Public domain](https://www.sqlite.org/copyright.html); pinned downloads in `desktop/fetch_runtime.py` |
| APSW | [APSW license](https://github.com/rogerbinns/apsw/blob/master/LICENSE); package notices retained |
| WebView2 SDK 1.0.4191.47 and Fixed Runtime 153.0.4234.48 | [Microsoft distribution terms](https://developer.microsoft.com/en-us/microsoft-edge/webview2/); SDK license in `licenses/`, runtime notices in `_internal/WebView2/` |
| DeepSeek Harness SDK/runtime 0.1.5rc1 | [MIT](https://github.com/deepseek-ai/deepseek-harness/blob/master/LICENSE) |
| websockets 15.0.1 | BSD-3-Clause; original LICENSE retained in package `.dist-info` |
| dnspython 2.9.0 | [ISC](https://github.com/rthalley/dnspython/blob/v2.9.0/LICENSE); bounded DNS / TLS queries; original package notices retained |
| MCP Python SDK 1.26.0 | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/LICENSE); package license retained |
| polylens-bilibili-mcp 0.13.0 selected read core | [Apache-2.0](https://github.com/liu-xindi/polylens-bilibili-mcp/tree/35b9c852342a9e8b8d09db78548d42d62b6f9d2a); original LICENSE and source hashes in `tulpa_bilibili/_vendor/`; upstream MCP server and social-write tools are not bundled |
| Segno 1.6.6 | BSD-3-Clause; local QR rendering, original package license retained |
| qq-bridge 群聊行为与小鲸鱼角色卡 | [MIT](https://github.com/Derpyu520/qq-bridge/blob/9df6a7e7fc5abcb36793337f778483bd442a3d2d/LICENSE); 小鲸鱼角色卡经 Tulpa 维护者按需改编，保留来源及许可；行为与工具协议单独适配，仅用于 MCP 持续群聊，见 `desktop/licenses/qq-bridge-MIT.txt`（发布包 `licenses/qq-bridge-MIT.txt`） |
| QQ reader (`chatlog-keeper`) | [MIT](https://github.com/labazhou2024/chatlog-keeper/blob/b55675779e50edec913fab9d891e4185c8f7c9ac/LICENSE); commit `b55675779e50edec913fab9d891e4185c8f7c9ac` |
| WeChat reader (`wechatauto-replica`) | [Apache-2.0](https://github.com/fanyuantaier/wechatauto-replica/blob/492a8fb70b95865613d6d8d9740323233dbfa197/LICENSE); commit `492a8fb70b95865613d6d8d9740323233dbfa197` |
| whisper.cpp b5130 and Whisper small q5_1 | [whisper.cpp MIT](https://github.com/ggml-org/whisper.cpp), [Whisper MIT](https://github.com/openai/whisper), [model distribution](https://huggingface.co/ggerganov/whisper.cpp); copies in `licenses/` |
| RapidOCR / PP-OCR models | [RapidOCR Apache-2.0](https://github.com/RapidAI/RapidOCR), [PaddleOCR Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR); model hashes pinned by the build |
| ONNXRuntime, OpenCV, Pillow, Docling and other Python packages | Original package licenses retained; see `build-manifest.json` for the installed versions |
| imageio-ffmpeg Python wrapper | [BSD-2-Clause](https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0); the bundled executable has a separate license, below |
| FFmpeg 7.1 essentials executable | **GPL-3.0-or-later**, [Gyan build](https://github.com/GyanD/codexffmpeg/releases/tag/7.1); `licenses/FFmpeg-GPL-3.0.txt` and `licenses/FFmpeg-build.txt` |
| Microsoft Visual C++ runtime | Unchanged app-local DLLs from the pinned Microsoft runtime distribution; Microsoft distribution terms |

## FFmpeg

This software uses an unmodified executable from [FFmpeg](https://ffmpeg.org/),
licensed under GPL version 3 or later. It runs as a separate process to decode
WeChat WXGF / HEVC images. Its license is not the BSD license of the Python wrapper.

The executable's source revision is
[`b08d7969c5`](https://github.com/FFmpeg/FFmpeg/commit/b08d7969c5).
The corresponding FFmpeg source archive is provided alongside the desktop ZIP
on the [Tulpa release page](https://github.com/fumingyang2004/Tulpa/releases/latest).
The build provider's full configuration and external library revisions are
reproduced without changes in `desktop/licenses/FFmpeg-build.txt` (in the portable
package: `licenses/FFmpeg-build.txt`). The original distribution, source links and
build information are available from [Gyan's build page](https://www.gyan.dev/ffmpeg/builds/)
and [FFmpeg's external library documentation](https://ffmpeg.org/general.html#External-libraries).
No restriction in Tulpa's license prevents replacing, modifying or reverse
engineering this component under its own license.

## Reader adaptations

`scripts/install_readers.py` fetches the pinned upstream modules and their licenses;
`scripts/reader_patches.py` contains the reproducible local adaptations. The reader
manifest records both original and installed hashes. QQ adaptations preserve long
text, use the isolated SQLite runtime, honor an explicit per-install data root,
and use bounded, chunked passive Windows key scanning with unchanged page-HMAC
acceptance (`scripts/qq_passive_keys.py`). The WeChat adaptation adds bounded legacy
key recovery. The modified WeChat file carries a modification notice. Reader
licenses remain in `tools/qq-reader/LICENSE` and `tools/wechat-reader/LICENSE`.

## SnowLuma

SnowLuma 2.1.1 may be downloaded from its official release or included as the
unchanged, SHA256-pinned complete runtime archive in an authorized Tulpa build.
The project owner reports written permission for this integration and bundling.
It is a separately licensed dependency, not covered by Tulpa's MIT license.
The [upstream license](licenses/SL_LICENSE.txt), copyright and original EULA /
privacy documents are retained. End users explicitly consent before deployment.
The official binary archive contains no Tulpa credentials or user configuration.

## Not bundled

The Bilibili module references Yueying 0.2.1 (MIT, commit
`e8fb72eabf9a2193b8d8cf5ce4e5c17de4edbcfe`) and bilibili-notify v0.10.1
(MIT, commit `49400e2b69b7c8f8af5b89ef8e460f0939cbbdfc`) for media flow and
native card protocol, without copying their code or bundling their services.
Local ASR reuses the existing optional Whisper.cpp model. No new hosted model
or API credential is required. See [Bilibili usage and limitations](BILIBILI_MCP.md).

NapCat, QQ and WeChat clients are not redistributed. No Microsoft / Office fonts
are redistributed. User-provided credentials,
chat exports, model answers and local account state are not part of a release.
