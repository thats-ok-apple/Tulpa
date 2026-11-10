# Linux 开发与打包

在可写目录解压当前 Linux 测试源码包，准备 Python 3.13 或 uv，运行 `./tulpa`。
依赖固定在 `requirements/linux.txt`、`requirements/tui.txt` 及它们引用的文件中。
完整组件安装用 `sh setup-linux.sh --full`；开发时只安装环境可用 `sh setup-linux.sh`。

## 目录与入口

- `tulpa`：统一入口，准备环境、启动后台和打开 TUI。
- `chatlocal/terminal_*.py`：CLI、本机客户端、启动器与终端后端。
- `chatlocal/tui/`：Textual 终端前端。
- `chatlocal/linux_*.py`、`native_sources.py`：Linux 组件、来源、服务与打包。
- `chatlocal/snowluma_*.py`：SL 连接、本地托管、Docker 托管及显示配置。
- `scripts/`：组件安装、资料读取和隔离检查。

前端调用 [终端 API](TERMINAL_API.md)，复用原业务接口的权限、预览与确认流程。
共享 Python 后端保留跨平台分支；Linux 发行清单单独选择启动入口、文档和辅助脚本。
模型参与的模块见 [模型与分工](LINUX_CONTRIBUTORS.md)。仓库根目录的 `README.md`
保留所有平台入口；打包和创建子实例时使用 `doc/LINUX.md` 作为包内的 `README.md`，
并仅收入 Linux 专用教程。已解压的 Linux 包可以直接再次打包。

## 检查

```sh
.venv/bin/python scripts/check_linux_distribution.py
.venv/bin/python scripts/check_linux_suite.py terminal_launcher terminal_backend terminal_integration
.venv/bin/python scripts/check_linux_suite.py
```

打包检查覆盖发行文件边界、文档链接和依赖引用。套件使用隔离数据和合成 OneBot，
不会向真实群发送消息或调用付费模型；它的单项日志位于 `.tmp/linux-acceptance/`。
可选组件装好后，可另运行 `.venv/bin/python scripts/check_linux_components.py`。
测试结果需记录实际架构，不把 ARM64 通过等同于 x86_64 实机通过。

## 生成干净源码包

```sh
./tulpa package ../Tulpa-linux-clean.tar.gz
```

输出必须为尚不存在的文件。此命令不需要后台运行；解压后仍用 `./tulpa` 启动。
发行清单由 `chatlocal/linux_distribution.py` 统一维护，包含运行代码、默认资源、
Linux 安装脚本、依赖清单、Linux 文档和相关许可证。
不包含账号数据、凭据、`.env`、虚拟环境、缓存、下载工具、容器镜像或其他平台的启动与构建文件。

仓库的测试须知位于 `doc/LINUX_TESTING.txt`，打包后映射为根目录 `TESTING.txt`。
旧包的 `README.linux.md`、`TESTING.txt` 或 `看我.txt` 仍可识别并重新打包；发行包的启动方式不变。

打包会使用当前源码，因此发布前应检查自己的代码、人格卡和模板是否有私人修改；
它不是把任意源文件中的私密内容自动删除的工具。不要直接压缩运行过的整个项目目录。
