#pragma once

#include "GMTIProcessor.hpp"

#include <cstddef>
#include <vector>

struct LowRadialVelocityFilterStats {
    std::size_t input_count = 0;
    std::size_t removed_low_velocity_count = 0;
    std::size_t removed_nonfinite_count = 0;
    std::size_t output_count = 0;
};

LowRadialVelocityFilterStats filterLowRadialVelocity(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double threshold_mps);

struct P38SlopeUpperBoundFilterStats {
    std::size_t input_count = 0;
    std::size_t removed_nonfinite_count = 0;
    std::size_t removed_upper_bound_count = 0;
    std::size_t output_count = 0;
};

// 在 P38 斜率的物理符号已由当前 CTDR 几何固定时，拒绝超过上界的候选。
// 必须由配置显式开启；使用最终实际参与定位的 p38_used_k，保证 CSV/协议一致。
P38SlopeUpperBoundFilterStats filterP38SlopeUpperBound(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double upper_bound_rad_per_hz);

struct AdjacentBeamDuplicateSuppressionStats {
    std::size_t input_count = 0;
    std::size_t removed_duplicate_count = 0;
    std::size_t output_count = 0;
};

// 在整周期检测写入 GMTIxx.bin 之前，按相邻波位、空间位置和径向速度
// 对重复观测执行非极大值抑制。保留幅度更大的观测；同一波位永不在此处合并。
AdjacentBeamDuplicateSuppressionStats suppressAdjacentBeamDuplicates(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double position_gate_m,
    double velocity_gate_mps);

struct MechanicalCpiDedupStats {
    std::size_t input_count = 0;
    std::size_t removed_duplicate_count = 0;
    std::size_t output_count = 0;
};

// Suppress duplicate observations created only by overlapping mechanical CPI
// windows. The physical beam id is deliberately ignored/invalid in this mode.
MechanicalCpiDedupStats deduplicateMechanicalDetections(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    const Config& cfg);
