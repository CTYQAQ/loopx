## Research split program

**12 个完整拆分 PR 已发布；#6881 已进入 main，#6882 已自动改向 main。** 本页继续记录原小 PR 路线、最新原提案的组合重叠，以及各项实际验证的版本。固定 14 PR 的本轮检查/审阅窗口为 **2026-10-20 00:14–00:15 UTC（北京时间 08:14–08:15）**；发布完成不等于全部合并门禁通过。

### 如何进入 main：先明确承载路线

原拆分采用普通依赖 PR：前一片尚未进入 main 时，后一片以它为 base，使审阅差异仅包含新增行为。最终每一片都应在前置进入 main 后，面向 main 取得相应资格再合并。实际已有先例：`loopx-agent` 于 **2026-10-19 18:12:37 UTC** 合并 #6881，merge `7f52528c9553088409253a8d37f24a37c91ad8da`；GitHub 于 **18:12:40 UTC** 自动把 #6882 的 base 改为 main。

新版 [#6784](https://github.com/loopx-project/loopx/pull/6784) 已由维护者重组为 **B1 + A1 + A2 + A3 foundation**，当前 head `b16aeb372d1033c1c7d17f3bf536b28db30a01cb`，与 #6882、#6897、#6898、#6899 存在实质重叠。最初拆分冻结来源 `8bf0265e113225d5abd3f5ee5f0f0f3d91c5c213` 与该新版需分别识别。

| 路线 | B1 / A1–A3 的承载入口 | 后继处理 |
| --- | --- | --- |
| **按原任务继续小 PR（本页默认建议）** | #6882、#6897、#6898、#6899 | 将新版 foundation 中有效且可隔离的修订回填所属片，保留作者和来源；依赖逐片进入 main |
| 采用新版 foundation | #6784 | 原四片保留来源映射；其余 PR 在基础进入 main 后，仅以自身未合入增量重定基线 |

同一功能选择一个合入入口。当前 #6784 仍有冲突及未通过检查，不因其已组合源码而认定可立即合并。更换承载路线需要先核对来源、独立修复和后继增量。

### 原小 PR 路线的具体操作

1. 先修当前片的有效评审意见，并把已变更的前置修订同步到其后继，复核每片仍只表达自己的增量。独立 B1 现在已经面向 main，但仍有三条未解决意见。
2. 当前片进入 main 后，核对下一片的 base；若尚未自动转向 main，在 PR 的 Edit 中修改 base。每次改向后重新查看 Files changed、冲突、当前 head 检查与审批，确认已合入的前置代码从审阅差异中消失。
3. 本仓库允许 merge commit、squash 与 rebase。对现有保留合并祖先的普通依赖链，**优先使用 merge commit** 能保留原提交身份，便于后继继续比较；主干保护与实际合并门槛仍由仓库判定。
4. 如维护者选择 squash/rebase，则按旧父提交边界，仅重放本片尚未合入的提交，并逐层更新后继；核对最终代码与单片差异后重新检查。单独改 base 不会自动完成这项整理。
5. 集成分支用于组合资格与来源追溯；各功能进入 main 继续遵循选定的 PR 承载路线和相应审批。

参考：[GitHub 合并策略](https://docs.github.com/en/pull-requests/reference/pull-request-merges) · [修改 PR base](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/changing-the-base-branch-of-a-pull-request)。

### 原小 PR 顺序与当前状态

| 顺序 | 完整切片 | 当前 base | source head | 本轮观察 |
| --- | --- | --- | --- | --- |
| 1 · CI | [#6881](https://github.com/loopx-project/loopx/pull/6881) · CI 发布资格 | main | `70ab394b57b8` | 已合入 main |
| 2 · B1 | [#6882](https://github.com/loopx-project/loopx/pull/6882) · 验收连续性 | main | `bdee6973fc0c` | 要求修改；3 个讨论 |
| 3 · B2 | [#6883](https://github.com/loopx-project/loopx/pull/6883) · 依赖等待与准入 | #6882 | `b74eb21b84ea` | 要求修改；1 个讨论 |
| 4 · B3 | [#6884](https://github.com/loopx-project/loopx/pull/6884) · Host 启动与派发 | #6883 | `b813ae0064bf` | 要求修改；2 个讨论 |
| 5 · B4 | [#6885](https://github.com/loopx-project/loopx/pull/6885) · Worker 恢复与读回 | #6884 | `9d905ca38664` | 失败/取消 + 待完成检查 |
| 6 · B5 | [#6889](https://github.com/loopx-project/loopx/pull/6889) · 证据比较与决策 | #6885 | `bb972c1f7d38` | 失败/取消 + 待完成检查 |
| 7 · A1 | [#6897](https://github.com/loopx-project/loopx/pull/6897) · Acceptance v2 消费者 | #6889 | `12863fe54b9b` | 失败/取消 + 待完成检查 |
| 8 · A2 | [#6898](https://github.com/loopx-project/loopx/pull/6898) · 累计 Outcome 与恢复 | #6897 | `e02915d0d180` | 检查未完成；待审批 |
| 9 · A3 | [#6899](https://github.com/loopx-project/loopx/pull/6899) · 证据缺口与原 Turn 结算 | #6898 | `da24c787a8f5` | 检查未完成；待审批 |
| 10 · C1 | [#6900](https://github.com/loopx-project/loopx/pull/6900) · Native 请求准入 | #6899 | `360c6ef15848` | 检查未完成；待审批 |
| 11 · C2 | [#6901](https://github.com/loopx-project/loopx/pull/6901) · 执行证据进入后继研究 | #6900 | `1b243b9ac1aa` | 检查未完成；待审批 |
| 12 · C3 | [#6902](https://github.com/loopx-project/loopx/pull/6902) · Native 反馈与等待恢复 | #6901 | `7368ca0286d5` | 检查未完成；待审批 |

### 已确认的下一步评审工作

- **B1 #6882**：修正[过时 brief 预算断言](https://github.com/loopx-project/loopx/pull/6882#discussion_r4317963822)，按 B1 自己的 owner 对齐 **10,800 / 9,000**；将[一次 Host 派发的跨层后置条件](https://github.com/loopx-project/loopx/pull/6882#discussion_r4317963831)与 canonical 连续性资格分开；在同一输出 owner 内[整理 metadata 裁剪复杂度](https://github.com/loopx-project/loopx/pull/6882#discussion_r4317963835)。新版 foundation 中三处确有相关源码修订，但包含 A1–A3 后的 **11,400 / 9,600** 预算及 v2 假设不能原样照抄到 B1。
- **B2 #6883**：在现有准入 owner 中[整理 quota route 的新增复杂度](https://github.com/loopx-project/loopx/pull/6883#discussion_r4317970461)，保留 Stop、健康、额度、原 receipt 和独立工作优先级。
- **B3 #6884**：优先修 [P1 同步 stdin 写入持有 Goal writer，响应期限未覆盖发送](https://github.com/loopx-project/loopx/pull/6884#discussion_r4318530017)。发送应在原启动边界内可限时/取消，部分发送失败须清理所属 native 进程后再释放，保障 Stop 与旧请求隔离；同时[整理 native Host owner 的新增复杂度](https://github.com/loopx-project/loopx/pull/6884#discussion_r4318530024)。
- 本轮上述判断来自固定 SHA 的独立源码审查；审阅中的测试数字没有被改写为本轮执行结果，原 threads 保持实际状态。#6838 当前来源也新增 changes requested（三个讨论）；#6784 的原有五个讨论与新版资格独立保留。

### 已发布与已验证的组合工作

- A1–A3、C1–C3 六个后续 PR 已补齐。A1 有测试断言 v2 迁移；B4 修复 fixture 在 HTTP 请求 drain 之后再退役 runtime、确认 child close 后才删除临时根；A3 后续仅两文件修复 Markdown 诊断预算，并向 C1–C3 保留祖先地传播。
- A3 修复原 renderer 在新增回归中 **2 failed / 3 passed**，修复后 **19 passed（19.72 秒）**；12 行真实 CLI 载体、24 次前后调用核对 **5.042 秒通过**，JSON 字节相等、task body 与语义规则保持，原 4200/6200 门槛不变。原 Windows 6332 超限的纯格式化推算可降至 6172；新 head 的 Windows 在观察窗口仍在运行/排队或尚未出现，推算不作为 CI 通过。
- [统一集成分支](https://github.com/loopx-project/loopx/tree/codex/split-research-integration-20261019) 已快进到 [`fe3b1f7b382e`](https://github.com/loopx-project/loopx/commit/fe3b1f7b382e30cdf691cbf0067a5d4e2f5b3b0b)，tree `6bbf110b1cb2535975b2389558a86dff73792926`。两父提交为原 `d1dc8ebfe7ff0ce888c8e7a6b7a5051413f29af2` 与最新 C3 `7368ca0286d503a93e50b26ab24755a5fb429b2f`；真实 Git merge-tree 零冲突，和 GitHub 构树完全一致；相对原集成仅有 A3 两文件修复，保留全部 12 source heads 与已验证冻结 main。
- 冻结 main 为 `45d742088a3f7896a0a008761f68d2437398bcd1`。本轮观察的 main `e4190c30d7f94886dddd8e06b899664df34120f9` 相比之已有 52 个提交、120 个文件变化，涉及 UI 与运行 owner，另行更新和验证后才能获得最新主干资格。原 `d1dc8` 的 supplied-candidate `in_sync` 收据保留历史绑定；本页不将旧收据标成当前 main 的合并资格。
- 原 `d1dc8/cb8` 与冻结完整来源、同一 main、明确 A1/B4 修复的 tree parity 已实际核对。历史唯一指标冲突按自动合并 `chat_store.py` 的 2067 实际行数结算；后继 A3 变更来自真实所属源提交，集成没有另加产品逻辑。
- 该原集成 tree 的 control-plane TypeScript **50.754 秒通过**，标准 Chat build（含 Dashboard TypeScript）**131.930 秒通过**；Outcome 原完整恢复 **97.616 秒通过**，独立正常首屏 **41.605 秒通过**，Worker readback **126.422 秒通过**，research-history 原完整功能场景 **116.004 秒通过**。[8 张可读截图和逐项验证摘要](https://github.com/loopx-project/loopx/tree/3083779b33f2b41c643f78266d41a462cf473673/docs/assets/research-split-20261019)已发布，拍摄版本保持 `d1dc8`；后继两文件修复没有改变这些 UI 输入。
- 本轮 A3 冻结来源的真实 File/SQLite 混合集成 **2 passed（1347.13 秒）**与 3 个 TS 测试文件通过；C1 原背压 File 单例 **31.05 秒通过**，C2 原完整 SQLite candidate→probe→新 Todo 实验 **338.81 秒通过**，C3 原完整 SQLite native result→原 Turn 恢复→改变后的实验 **65.65 秒通过**。它们分别绑定原来源，不是最新 source head 的完整矩阵。
- A3 补丁前的逐片 RFC 双语检查 **12/12**、唯一历史源提交 DCO **14/14**通过；后续两文件补丁另通过所属 staged gate。新源链实际核对 **18 个唯一提交，18/18 作者姓名和邮箱均有匹配 Signed-off-by**。本轮恢复的未修改 integration owner 对真实已发布候选返回 **preview_ready（execute=false）**；新建 plan 的状态仍是 never_synced，未重新签发 in_sync，也未执行完整 CLI bootstrap。对象一致、签署核对、候选预览与最终同步状态分别记载。

### 仍保留的资格缺口

首次 Outcome fixture 在原 90 秒启动窗口失败（总计 92.463 秒），后续通过使用正常缓存预热，未取得冷启动资格。research-history 的新中文截图缺 CJK 字形，功能通过与合格新图分开记录，完整字形捕获仍待补。A1 feedback CLI、本轮未跑的 C2/C3 File 参数、专门重放/并发及完整新 head Python/native 矩阵继续按未跑项披露。

所有实际输入为 synthetic 本地 authority/HTTP/CLI；没有外部模型、installed App 或正式 benchmark 收益声明。已发布验证摘要记录本轮实际运行范围，原临时 raw logs 随执行环境回收后不可读，未被重建成原始收据。

## 可读回记录

- [当前 source refs](source-manifest.json)
- [固定 14 PR 的检查与评审快照](landing-snapshot.json)
- [六项静态评审判断](review-findings.json)
- [最终集成对象、补丁、UI 输入和身份核对](integration-source-proof.json)
- [原构建、浏览器和定向测试执行摘要](validation-summary.json)

