# zcode-delegate

把本地 ZCode CLI（桌面版自带、headless `-p` 模式）封装成可委托的子 worker，供父 agent 调度：[github.com/techdou/zcode-delegate](https://github.com/techdou/zcode-delegate)。能力与 codex-delegate 同族：

- 四档权限模式映射（ask/review → `--mode plan`，edit → `--mode edit`，agent → `--mode yolo`）
- `--session auto|continue|new|off` 会话续接（`--resume sess_...` + 本地注册表，resume 后校验 sessionId，不一致即 fail closed）
- `--meta-output` 缓存统计（直接取 provider 上报的 `cacheReadTokens`，不推测）
- 整树超时击杀（Windows `taskkill /T /F`）
- `--doctor` / `--dry-run` / `--extra` 透传

与 codex-delegate 的关键差异：zcode headless **没有 OS 级沙箱**，`agent` 模式等于完全信任；CLI 入口在桌面版安装目录内，桌面更新会换版本。详见 [SKILL.md](SKILL.md)。

## 部署（新设备）

skill 依赖两个全局启动器（提供 `zcode` 命令）：把本目录 `launchers/zcode`（Git Bash）和 `launchers/zcode.cmd`（cmd/PowerShell）复制到 `~/.local/bin/`。两者都是候选自探测（`ZCODE_CLI_ENTRY` > 常见桌面版安装位），换机器无需改脚本。前提：本机装有 ZCode Desktop（CLI 内置其中）+ Node。

## 用法示例：

```bash
python scripts/run.py --doctor
python scripts/run.py --mode ask --cwd <workspace> --task "<bounded task>"
python scripts/run.py --session auto --session-key op-42 --mode edit --cwd <workspace> --task-file task.md --meta-output .zcode-run-meta.json
```
