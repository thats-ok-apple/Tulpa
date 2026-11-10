#!/bin/sh
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TULPA_ROOT"
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' '此发行包需要 Linux 环境。' >&2
    exit 1
fi
umask 077
mkdir -p .tmp data imports
TULPA_REQUIREMENTS=requirements/linux.txt
TULPA_COMPONENTS=0
case "${1:-}" in
    --full) TULPA_REQUIREMENTS=requirements/linux-full.txt; TULPA_COMPONENTS=1 ;;
    --help) printf '%s\n' './setup-linux.sh [--full]  安装完整后端；--full 另装读取器、OCR、Office 和语音。'; exit 0 ;;
    '') ;;
    *) printf '%s\n' '未知参数，请使用 --help。' >&2; exit 2 ;;
esac
if command -v flock >/dev/null 2>&1; then
    exec 9>.tmp/setup-linux.lock
    flock 9
fi
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.13 .venv
    elif command -v python3.13 >/dev/null 2>&1; then
        python3.13 -m venv .venv
    else
        printf '%s\n' '需要 Python 3.13 或 uv。安装其中之一后重新运行本脚本。' >&2
        exit 1
    fi
fi
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))'; then
    printf '%s\n' '现有 .venv 不是 Python 3.13，请使用 Python 3.13 或 uv 重新准备虚拟环境。' >&2
    exit 1
fi
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python --excludes requirements/linux-excludes.txt -r "$TULPA_REQUIREMENTS" -r requirements/tui.txt
else
    .venv/bin/python -m ensurepip --upgrade
    .venv/bin/python -m pip install -r "$TULPA_REQUIREMENTS" -r requirements/tui.txt
fi
if [ "$TULPA_COMPONENTS" = 1 ]; then
    .venv/bin/python scripts/install_readers.py
    .venv/bin/python scripts/setup_linux_ocr.py
    .venv/bin/python scripts/setup_voice.py
fi
printf '%s\n' '准备完成。运行 ./tulpa 即可自动启动后台并打开终端界面。'
