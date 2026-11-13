# R22-S1c 首轮试跑失效说明

日期：2026-10-29

状态：`INVALID_NOT_EVALUABLE`

首轮两条十期 Production 分支虽然退出码为0，但处理链将 raw/CSI range-phase
sidecar 写入固定的 `period050_beam050` 文件。文件名没有包含扫描周期；并行或多期
处理时会由后写入者覆盖，无法判定留下的相位向量属于 GMTI05 还是其他周期。
所以首轮生成的共同相位反事实不能与目标组绑定，不能用于因果判断。

首轮 manifest：`outputs/r22_s1c_shared_off_phase_20261029/s1c_manifest.json`

SHA-256：`66a19e81c3e6702cef061d7675803c78c82462bdbebd194467eb844c18c2da0a`

首轮生成的比较 CSV 和 reanalysis manifest 已移入本目录；同一字节副本及原始
runner/analyzer 保存在：
`outputs/r22_s1c_shared_off_phase_20261029/attempt1_invalid_phase_vector_scope/`。
这些材料只用于审计首轮为何失效。由它们算出的 support 数字不作为 S1c 结果，
也不得与 v2 结果合并。

修正方式是仅向每个分支提供原始 `period_0004.bin`，并在 manifest 中记录
`source_period_index_zero_based=4 / source_period_label=GMTI05` 到 Production
`production_period_id=1` 的映射。v2 协议见上级
[`S1C_SHARED_OFF_PHASE_PROTOCOL.md`](../../S1C_SHARED_OFF_PHASE_PROTOCOL.md)。
