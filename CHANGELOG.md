# Changelog

## 0.2.0 — 2026-10-26

- 泛化入口发现：`--entry` > `ZCODE_CLI_ENTRY` > 各平台桌面版安装候选（Windows LOCALAPPDATA/Program Files/自定义盘符、macOS /Applications、Linux /opt、~/ZCode）> PATH 上的 zcode（.cmd/.bat 经 `cmd /c` 包装）。`~/.local/bin` 两个 shim 同步改为候选自探测，换设备/换安装位无需改脚本。
- node 发现：`NODE_EXE` > PATH；POSIX 上子进程 `start_new_session` + `os.killpg` 实现整树击杀（此前仅 Windows 有树杀）。
- `--doctor` 输出 launch 前缀与 node 解析结果。

## 0.1.0 — 2026-10-26

- 初版：模式映射（ask/review/edit/agent → plan/plan/edit/yolo）、headless `-p` 执行、`--json` 结果解析（sessionId + usage + projection）。
- 会话续接：`--session auto|continue|new|off` + `state/registry.json` 本地注册表（按 session key + cwd 作用域），resume 后 sessionId 不匹配 fail closed（exit 5）。
- 缓存统计：`--meta-output` 输出 provider 上报的 `cacheReadTokens` 与命中率。
- 超时：秒级整数，超时后 `taskkill /T /F` 击杀整棵子进程树（exit 4）。
- 实测基线：CLI 0.17.0（ZCode Desktop 内置），resume 上下文连续性与跨进程缓存复用均已验证。
