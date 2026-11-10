# Linux 源码包的第三方组件

根目录的 [MIT License](../LICENSE) 适用于 Tulpa 自有代码。Linux 测试包包含源码、
默认资源和依赖清单；Python 环境、可选工具、模型与容器镜像由使用者安装时下载，
分别遵循各自许可。依赖版本以 `requirements/`、安装脚本及组件 manifest 为准。
安装后的 Python 许可证保留在 `.venv/` 内各包的 `.dist-info/` 目录中。

| 组件 | 许可与来源 |
| --- | --- |
| CPython 3.13 | [PSF](https://docs.python.org/3/license.html)，由系统或 uv 提供 |
| SQLite / APSW | [SQLite public domain](https://www.sqlite.org/copyright.html)、[APSW license](https://github.com/rogerbinns/apsw/blob/master/LICENSE) |
| DeepSeek Harness SDK/runtime | [MIT](https://github.com/deepseek-ai/deepseek-harness/blob/master/LICENSE)，版本固定在 Linux 依赖清单 |
| MCP Python SDK | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/LICENSE) |
| Textual / Rich | [Textual MIT](https://github.com/Textualize/textual/blob/main/LICENSE)、[Rich MIT](https://github.com/Textualize/rich/blob/master/LICENSE) |
| textual-image | [LGPL-3.0-or-later](https://github.com/lnqs/textual-image/blob/main/LICENSE.txt)，通过 Python 依赖安装，用于终端图片显示 |
| qq-bridge 群聊行为与小鲸鱼角色卡 | [原作 MIT](https://github.com/Derpyu520/qq-bridge/blob/9df6a7e7fc5abcb36793337f778483bd442a3d2d/LICENSE)，随包保留 [许可副本](../licenses/qq-bridge-MIT.txt) |
| Linux QQ / 微信发现与取钥辅助：chatlog-keeper | [MIT](https://github.com/labazhou2024/chatlog-keeper/blob/2225294fd0761e10ce15540f21e7dbb2ac304c3c/LICENSE)，固定提交 `2225294fd0761e10ce15540f21e7dbb2ac304c3c` |
| 微信读取模块：wechatauto-replica | [Apache-2.0](https://github.com/fanyuantaier/wechatauto-replica/blob/492a8fb70b95865613d6d8d9740323233dbfa197/LICENSE)，固定提交 `492a8fb70b95865613d6d8d9740323233dbfa197` |
| whisper.cpp / Whisper 模型 | [whisper.cpp MIT 副本](../licenses/whisper-cpp-MIT.txt)、[Whisper MIT 副本](../licenses/whisper-model-MIT.txt)；可选安装脚本固定源码与模型哈希 |
| RapidOCR / PP-OCR 模型 | [RapidOCR Apache-2.0](https://github.com/RapidAI/RapidOCR)、[PaddleOCR Apache-2.0 副本](../licenses/PaddleOCR-Apache-2.0.txt) |
| ONNXRuntime、OpenCV、Pillow、Docling 等 Python 包 | 保留安装包中的原始许可证和声明 |
| imageio-ffmpeg 包与 FFmpeg 可执行文件 | [包装器 BSD-2-Clause](https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0)；FFmpeg 有独立许可，见下文 |
| 可选 Linux 构建工具 Zig、CMake、Ninja | 各自发行包的许可证；固定版本见 `requirements/linux-build.txt` |

## 人格与设计来源

小鲸鱼人格改编自 qq-bridge，来源记录保存在 `chatlocal/prompts/mcp_chat/`。
行为协议按 Tulpa 的授权和工具机制单独适配；人格不会替换底层模型。
表情收藏、语义笔记及候选反馈参考 [QQ-agent](https://github.com/K0nd1us/QQ-agent)，
Tulpa 独立实现其权限与媒体验证。语言学习与回复分层参考
[MaiBot](https://github.com/Mai-with-u/MaiBot) 的公开机制，没有复制其 GPL 代码或提示词。

## 读取器与 FFmpeg

`scripts/install_readers.py` 获取固定版本的上游读取模块与许可证；
`scripts/reader_patches.py` 记录本地改动，安装 manifest 保存来源和文件哈希。
读取器许可证安装到 `tools/qq-reader/LICENSE` 和 `tools/wechat-reader/LICENSE`。

FFmpeg 通过独立进程处理媒体。本源码包不附带 FFmpeg 二进制；Linux 安装时取得的
imageio-ffmpeg 发行包或使用者自己的 FFmpeg 应保留其对应许可、构建信息和源码获取方式。
[FFmpeg](https://ffmpeg.org/legal.html) 的许可取决于实际构建选项，不能用 Python 包装器的
BSD 许可代替，也不能套用其他平台二进制的构建声明。

## 独立安装的服务与资料

SnowLuma、QQ、微信客户端不随 Tulpa 源码包分发。Docker 托管仅在明确确认后，将
官方镜像下载到使用者本机 Docker 引擎；镜像版本与摘要固定于 `chatlocal/snowluma_manifest.json`。
镜像中的组件仍遵循各自许可与协议，见
[官方 Docker 框架](https://github.com/SnowLuma/SnowLuma.Docker.Framework)。
本包不含镜像层、客户端二进制、字体、用户凭据、聊天导出、模型回答或登录数据。
