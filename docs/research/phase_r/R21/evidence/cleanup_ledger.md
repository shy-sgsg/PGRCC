# R21 本地二进制证据清理台账

日期：2026-10-27。此次清理只作用于 R21 两个指定 Native CUDA run 目录，不触碰其他阶段证据或 `outputs/` 历史目录。

## 保留前置检查

- 文本审计包：`r21_cuda_text_audit_evidence_20261027.zip`，2,109 entries、52,416,987 uncompressed bytes、SHA-256 `e1907ada95980a7a6adfec0e1a5e44e06ec645f8b13c5e1c661f4385f58f535a`；ZIP CRC 全部通过。
- 清理前已从 Stage1 protocol IQ 重算 B→A0 F2 closure；清理后再次运行 `python3 scripts/phase_r/r21_finalize_evidence.py`，同样成功重建 17 CSV、6 SVG，且 6 个 seed×role 的 B F2 复数距离仍可重新计算。
- 清理后对 [`r21_retained_binary_manifest.json`](inputs/r21_retained_binary_manifest.json) 的 732 个保留文件逐一复核 SHA-256 和字节数：0 mismatch。

## 保留内容

| 类别 | 数量 | 用途 |
|---|---:|---|
| A0 与 B Stage1 protocol IQ | 36 files（每 seed 18） | ON/OFF/TO、三个 period 的 F1/F2 物理闭合重算输入 |
| 六条件 ON `input_merged.bin` | 12 files（每 seed 6） | A0、B、Known、R9 fixed、Huber、M1 的代表性校正后 Production 复数输入 |
| 六条件 ON CSI NPY | 684 files（每 seed 342） | 三 period 的 CSI 前后复数/功率、通道和 phase-model 残差数组 |
| **总计** | **732 files** | manifest 记录每个相对路径、字节数和 SHA-256；二进制留在本机，不进入 Git |

以上文件的 apparent byte 总数为 `4,846,896,864`；因部分路径有硬链接，实际磁盘 block 用量不等于 apparent bytes。其他 runtime XML、Stage2 几何、truth、Production log、CSI/CFAR 摘要、CFAR map PNG、逐机会与 TrackManager 记录保留在本地文本树和压缩审计 ZIP 中。

## 删除内容与容量

清理目标仅为：

- `evidence/cuda_pilot/seed1001_gatefixed_6f261b2/`
- `evidence/cuda_pilot/seed1002_0957f49/`

对这两次已归档 run，最终共移除 1,794 个 `.bin`/`.npy` 文件或 alias entry，删除不在白名单中的可再生输入副本、分支 merged copies 和其他角色/中间 CSI arrays；不删除报告、manifest、配置、XML、日志、CSV、PNG 或闭合必需的 A0/B 原始输入。原工作区 `du -sh` 约 `16G`；清理后约 `6.7G`，低于 `20G` 上限。精确清理后目录 apparent size 为 `6,937,243,932` bytes。

执行期间首轮清理器遇到 prepared-input symlink 的别名路径并中止；检查确认保留白名单文件均在，随后改用词法路径逐项 unlink，删除残留别名。最终两个 run 下 732 个白名单文件全部完整，0 个 broken symlink，文本审计 ZIP CRC 通过。没有通过覆盖基准、降低验证范围或删除历史唯一文字审计来腾空间。

## 复核入口

```bash
python3 scripts/phase_r/r21_finalize_evidence.py
python3 -m pytest -q tests/test_r21_finalize_evidence.py
```

完整保留清单：[`inputs/r21_retained_binary_manifest.json`](inputs/r21_retained_binary_manifest.json)。
