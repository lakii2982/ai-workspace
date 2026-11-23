#!/bin/sh
set -eu
core=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(uname -s)" = Darwin ] || { echo "Use install.sh on Linux."; exit 1; }
workspace=$(osascript -e 'on run argv' -e 'set defaultPath to item 1 of argv' -e 'return text returned of (display dialog "工作空间目录（可粘贴已有目录）" default answer defaultPath with title "AI WorkSpace 安装")' -e 'end run' "$core/../Workspace")
clients=$(osascript - "$core" <<'APPLESCRIPT'
on run argv
set names to {"Codex", "Claude Desktop", "WorkBuddy", "豆包办公", "DeepSeek Harness"}
set identifiers to {"com.openai.codex", "com.anthropic.claudefordesktop", "com.tencent.workbuddy.mac", "DoubaoWork", "com.deepseek.dsh"}
set valuesList to {"codex", "claude-desktop", "workbuddy", "doubao", "dsh"}
set detected to {}
repeat with i from 1 to count of names
    try
        set bundle to item i of identifiers
        if i = 4 then
            path to application "DoubaoWork"
        else
            tell application "Finder" to get application file id bundle
        end if
        set end of detected to item i of names
    end try
end repeat
set selected to choose from list names with title "AI WorkSpace 安装" with prompt "选择桌面 Agent。已识别的客户端已勾选。允许这些客户端调用本地工作空间的日常工具。" default items detected with multiple selections allowed
if selected is false then return ""
set ids to {}
repeat with i from 1 to count of names
    if selected contains (item i of names) then set end of ids to item i of valuesList
end repeat
set AppleScript's text item delimiters to ","
return ids as text
end run
APPLESCRIPT
)
[ -n "$clients" ] || exit 0
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    case "$(uname -m)" in
        arm64) target=aarch64-apple-darwin ;;
        x86_64) target=x86_64-apple-darwin ;;
        *) exit 1 ;;
    esac
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    asset="uv-$target.tar.gz"
    endpoint=https://github.com/astral-sh/uv/releases/download/0.8.22
    curl -fsSL "$endpoint/$asset" -o "$stage/$asset"
    curl -fsSL "$endpoint/$asset.sha256" -o "$stage/checksum"
    (cd "$stage" && shasum -a 256 -c checksum && tar -xzf "$asset")
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$stage/uv-$target/uv" "$HOME/.local/bin/uv"
fi
exec "$core/install.sh" "$workspace" --agent "$clients"
