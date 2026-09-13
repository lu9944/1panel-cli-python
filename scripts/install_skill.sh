#!/usr/bin/env bash
# 将 1panel-cli(Python 版)安装/更新到 opencode skill 目录。
#
# 用法:
#   scripts/install_skill.sh [目标目录]
# 默认目标: ~/.config/opencode/skills/1panel-skill
#
# 说明:
# - 覆盖 SKILL.md / commands.md / README.md / .env.example / bin/1panel-cli / panel_cli/;
# - 保留目标目录已有的 .env(真实凭据)与 bin/.env;
# - 清理旧版 Rust/dev-v2 的参考文件(api-dev-v2.json / compatibility-dev-v2.md)。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/.config/opencode/skills/1panel-skill}"

if [[ ! -f "$SRC/panel_cli/main.py" ]]; then
  echo "错误: 未找到 $SRC/panel_cli/main.py,请在 1panel-cli-python 仓库内运行" >&2
  exit 1
fi

mkdir -p "$DEST/bin" "$DEST/references"

# Python 包(含 data/api-v2.2.5.json)
rm -rf "$DEST/panel_cli"
cp -r "$SRC/panel_cli" "$DEST/panel_cli"
find "$DEST/panel_cli" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 入口与文档
cp "$SRC/bin/1panel-cli" "$DEST/bin/1panel-cli"
chmod +x "$DEST/bin/1panel-cli"
cp "$SRC/SKILL.md" "$DEST/SKILL.md"
cp "$SRC/commands.md" "$DEST/commands.md"
cp "$SRC/README.md" "$DEST/README.md"
cp "$SRC/.env.example" "$DEST/.env.example"
cp "$SRC/.env.example" "$DEST/bin/.env.example"

# 清理旧版本残留(exec 功能与说明文档已移除)
rm -f "$DEST/panel_cli/exec.py" "$DEST/panel_cli/ws.py"
rm -rf "$DEST/docs"

# 参考资料:替换为 v2.2.5 基线
cp "$SRC/references/compatibility-v2.2.5.md" "$DEST/references/"
cp "$SRC/panel_cli/data/api-v2.2.5.json" "$DEST/references/api-v2.2.5.json"
rm -f "$DEST/references/api-dev-v2.json" "$DEST/references/compatibility-dev-v2.md"

echo "已安装到: $DEST"
echo "验证: $DEST/bin/1panel-cli --version"
