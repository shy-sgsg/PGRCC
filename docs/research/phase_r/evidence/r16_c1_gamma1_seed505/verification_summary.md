# R16 验证记录

| 检查 | 命令/方法 | 实际结果 |
|---|---|---|
| Native Release build | `cmake --build build -j4`，CMakeCache `USE_COREX=OFF` | exit 0；生成的 `GMTI_pipe_core` SHA-256 见 `provenance.json` |
| CUDA Production | 见 `provenance.json` 的完整 `runner_command` | 本机 RTX 3050 Laptop，9 个 A0/C0/C1 × OFF/ON/TO branches；runner exit 0；9/9 branch passed |
| 专项评价 | 见 `provenance.json` 的完整 `evaluator_command` | exit 0；`evaluated`；27/27 CFAR final-union map/geometry audits passed |
| Python 定向测试 | `python3 -m pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_phase_r13_minimal_baseline_pilot.py` | 66 passed；控制台输出保存在 `verification_pytest.log` |
| Python 编译检查 | `python3 -m py_compile scripts/run_production_hierarchical_calibration_pilot.py scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py tests/test_production_hierarchical_calibration_pilot.py tests/test_phase_r13_minimal_baseline_pilot.py` | exit 0；见 `verification_py_compile.log` |
| Evidence package audit | JSON parse、Stage2 counter、adapter contract、R14/R16 input hashes、CFAR audit 与 gzip decompression SHA 对照 | 390 packets / 1,952,655 samples；9/9 输入 SHA 一致；27/27 地图解压 SHA 与原 SHA 一致；9 个 runtime XML/log 完整；证据包未保留 raw `.bin`、`.npy` 或 `.f32` |
| Markdown link audit | 检查 R16 report/design/direction/evidence/nav/master/status/decision docs 的本地目标 | 179 个本地链接检查通过 |
| R16 临时 root cleanup | 精确删除 `.scratch/r16_c1_gamma1_seed505_20261025/` 后 `test ! -e` | 目标不存在；清理对象和容量见 `cleanup_ledger.md` |

单 seed `PERFORMANCE_VERIFIED=false`。统计 Pfa 与 radial velocity 保持 `NOT_EVALUABLE`。跨 R14 的不同 Production binary 限制见结果报告。
