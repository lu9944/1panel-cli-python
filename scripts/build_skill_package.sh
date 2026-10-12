#!/usr/bin/env bash
# 构建技能发布包:dist/1panel-skill/(可安装目录)与 dist/1panel-skill-<version>.zip。
#
# 用法:
#   scripts/build_skill_package.sh [dist目录]     # 默认 <仓库>/dist
#
# 说明:
# - 复制面板 CLI、文档、references 与 source/ 源码快照(等价 scripts/install_skill.sh 的安装内容);
# - 生成面向终端用户的 install.sh(自动安装到 opencode / Claude Code 技能目录);
# - 有 zip 命令则用 zip,否则回退 python3 zipfile;
# - 在 CI 中运行时(设置了 $GITHUB_ENV)会导出 VERSION 供后续步骤使用。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="${1:-$SRC/dist}"
VERSION="$(python3 -c "import tomllib; print(tomllib.load(open('$SRC/pyproject.toml','rb'))['project']['version'])")"
PKG="$DIST/1panel-skill"

rm -rf "$PKG"
"$SRC/scripts/install_skill.sh" "$PKG"

cat > "$PKG/install.sh" <<'EOF'
#!/usr/bin/env bash
# 将本技能包安装到 AI 工具的 skills 目录。
# 用法: bash install.sh [目标目录]
# 默认:已存在 ~/.config/opencode/skills 时装到 opencode,否则尝试 Claude Code,
#       都不存在时使用 opencode 默认路径。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ge 1 ]]; then
  TARGET="$1"
elif [[ -d "$HOME/.config/opencode/skills" ]]; then
  TARGET="$HOME/.config/opencode/skills/1panel-skill"
elif [[ -d "$HOME/.claude/skills" ]]; then
  TARGET="$HOME/.claude/skills/1panel-skill"
else
  TARGET="$HOME/.config/opencode/skills/1panel-skill"
fi

if [[ "$(cd "$TARGET" 2>/dev/null && pwd || true)" == "$SRC" ]]; then
  echo "当前目录就是目标目录: $TARGET"
  exit 0
fi

mkdir -p "$TARGET/bin" "$TARGET/references"
rm -rf "$TARGET/panel_cli" "$TARGET/references"
cp -r "$SRC/panel_cli" "$TARGET/panel_cli"
cp -r "$SRC/references/." "$TARGET/references/"
if [[ -d "$SRC/source" ]]; then
  rm -rf "$TARGET/source"
  cp -r "$SRC/source" "$TARGET/source"
else
  echo "警告: 技能包未包含 source/ 源码参考" >&2
fi
cp "$SRC/SKILL.md" "$SRC/commands.md" "$SRC/README.md" "$SRC/.env.example" "$TARGET/"
cp "$SRC/bin/1panel-cli" "$TARGET/bin/1panel-cli"
cp "$SRC/.env.example" "$TARGET/bin/.env.example"
chmod +x "$TARGET/bin/1panel-cli"
find "$TARGET/panel_cli" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

echo "已安装到: $TARGET"
echo "验证: $TARGET/bin/1panel-cli --version"
EOF
chmod +x "$PKG/install.sh"

find "$PKG/panel_cli" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
"$PKG/bin/1panel-cli" --version

ZIP="$DIST/1panel-skill-${VERSION}.zip"
rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  ( cd "$DIST" && zip -qr "$(basename "$ZIP")" 1panel-skill )
else
  python3 - "$DIST" "$VERSION" <<'PY'
import os
import sys
import zipfile

dist, version = sys.argv[1], sys.argv[2]
root = os.path.join(dist, "1panel-skill")
out = os.path.join(dist, f"1panel-skill-{version}.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            zf.write(full, os.path.relpath(full, dist))
print("zip:", out)
PY
fi

echo "VERSION=$VERSION"
ls -l "$ZIP"
sha256sum "$ZIP" 2>/dev/null || true
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "VERSION=$VERSION" >> "$GITHUB_ENV"
fi
