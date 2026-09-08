#include "low_radial_velocity_filter.hpp"

#include <cmath>
#include <algorithm>
#include <numeric>
#include <stdexcept>

LowRadialVelocityFilterStats filterLowRadialVelocity(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double threshold_mps)
{
    if (mt_records.size() != detections.size() * 8U) {
        throw std::runtime_error(
            "low radial velocity filter: detection/MT record count mismatch");
    }
    LowRadialVelocityFilterStats stats;
    stats.input_count = detections.size();
    if (!enabled) {
        stats.output_count = stats.input_count;
        return stats;
    }

    std::vector<GMTIOutput::DetectionCsvRecord> retained_detections;
    std::vector<double> retained_mt;
    retained_detections.reserve(detections.size());
    retained_mt.reserve(mt_records.size());
    for (std::size_t i = 0; i < detections.size(); ++i) {
        const double radial_velocity_mps = detections[i].radial_velocity_mps;
        if (!std::isfinite(radial_velocity_mps)) {
            ++stats.removed_nonfinite_count;
            continue;
        }
        if (std::abs(radial_velocity_mps) < threshold_mps) {
            ++stats.removed_low_velocity_count;
            continue;
        }
        retained_detections.push_back(detections[i]);
        retained_mt.insert(retained_mt.end(),
                           mt_records.begin() + static_cast<std::ptrdiff_t>(i * 8U),
                           mt_records.begin() + static_cast<std::ptrdiff_t>((i + 1U) * 8U));
    }
    detections.swap(retained_detections);
    mt_records.swap(retained_mt);
    stats.output_count = detections.size();
    return stats;
}

P38SlopeUpperBoundFilterStats filterP38SlopeUpperBound(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double upper_bound_rad_per_hz)
{
    if (mt_records.size() != detections.size() * 8U) {
        throw std::runtime_error(
            "P38 slope upper-bound filter: detection/MT record count mismatch");
    }

    P38SlopeUpperBoundFilterStats stats;
    stats.input_count = detections.size();
    if (!enabled) {
        stats.output_count = stats.input_count;
        return stats;
    }

    std::vector<GMTIOutput::DetectionCsvRecord> retained_detections;
    std::vector<double> retained_mt;
    retained_detections.reserve(detections.size());
    retained_mt.reserve(mt_records.size());
    for (std::size_t i = 0; i < detections.size(); ++i) {
        const double slope = detections[i].p38_used_k;
        if (!std::isfinite(slope)) {
            ++stats.removed_nonfinite_count;
            continue;
        }
        if (slope > upper_bound_rad_per_hz) {
            ++stats.removed_upper_bound_count;
            continue;
        }
        retained_detections.push_back(detections[i]);
        retained_mt.insert(retained_mt.end(),
                           mt_records.begin() + static_cast<std::ptrdiff_t>(i * 8U),
                           mt_records.begin() + static_cast<std::ptrdiff_t>((i + 1U) * 8U));
    }
    detections.swap(retained_detections);
    mt_records.swap(retained_mt);
    stats.output_count = detections.size();
    return stats;
}

AdjacentBeamDuplicateSuppressionStats suppressAdjacentBeamDuplicates(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    bool enabled,
    double position_gate_m,
    double velocity_gate_mps)
{
    if (mt_records.size() != detections.size() * 8U) {
        throw std::runtime_error(
            "adjacent-beam duplicate suppression: detection/MT record count mismatch");
    }

    AdjacentBeamDuplicateSuppressionStats stats;
    stats.input_count = detections.size();
    if (!enabled || detections.size() < 2U) {
        stats.output_count = stats.input_count;
        return stats;
    }

    // 以幅度从高到低处理，同一簇始终保留最强观测；当幅度无效时保留
    // 原始顺序靠前者，从而保证输出可重复。严格要求不同且相邻的波位，
    // 同一波位的近距离双目标（例如 5 m 距离分辨测试）不会在这里合并。
    std::vector<std::size_t> order(detections.size());
    std::iota(order.begin(), order.end(), 0U);
    std::stable_sort(order.begin(), order.end(), [&detections](std::size_t lhs, std::size_t rhs) {
        const double a = detections[lhs].amplitude;
        const double b = detections[rhs].amplitude;
        const bool a_ok = std::isfinite(a) && a > 0.0;
        const bool b_ok = std::isfinite(b) && b > 0.0;
        if (a_ok != b_ok) return a_ok;
        return a_ok && b_ok ? a > b : false;
    });

    std::vector<bool> keep(detections.size(), true);
    for (std::size_t oi = 0; oi < order.size(); ++oi) {
        const std::size_t i = order[oi];
        if (!keep[i]) continue;
        const auto& kept = detections[i];
        if (!std::isfinite(kept.e) || !std::isfinite(kept.n) ||
            !std::isfinite(kept.radial_velocity_mps) || kept.beam_id < 0) {
            continue;
        }
        for (std::size_t oj = oi + 1U; oj < order.size(); ++oj) {
            const std::size_t j = order[oj];
            if (!keep[j]) continue;
            const auto& candidate = detections[j];
            if (candidate.beam_id < 0 ||
                std::abs(candidate.beam_id - kept.beam_id) != 1 ||
                !std::isfinite(candidate.e) || !std::isfinite(candidate.n) ||
                !std::isfinite(candidate.radial_velocity_mps)) {
                continue;
            }
            const double distance_m = std::hypot(candidate.e - kept.e, candidate.n - kept.n);
            if (distance_m > position_gate_m ||
                std::abs(candidate.radial_velocity_mps - kept.radial_velocity_mps) > velocity_gate_mps) {
                continue;
            }
            keep[j] = false;
            ++stats.removed_duplicate_count;
        }
    }

    if (stats.removed_duplicate_count != 0U) {
        std::vector<GMTIOutput::DetectionCsvRecord> retained_detections;
        std::vector<double> retained_mt;
        retained_detections.reserve(detections.size() - stats.removed_duplicate_count);
        retained_mt.reserve((detections.size() - stats.removed_duplicate_count) * 8U);
        for (std::size_t i = 0; i < detections.size(); ++i) {
            if (!keep[i]) continue;
            retained_detections.push_back(detections[i]);
            retained_mt.insert(retained_mt.end(),
                               mt_records.begin() + static_cast<std::ptrdiff_t>(i * 8U),
                               mt_records.begin() + static_cast<std::ptrdiff_t>((i + 1U) * 8U));
        }
        detections.swap(retained_detections);
        mt_records.swap(retained_mt);
    }
    stats.output_count = detections.size();
    return stats;
}

MechanicalCpiDedupStats deduplicateMechanicalDetections(
    std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::vector<double>& mt_records,
    const Config& cfg)
{
    if (mt_records.size() != detections.size() * 8U) {
        throw std::runtime_error(
            "mechanical CPI deduplication: detection/MT record count mismatch");
    }
    MechanicalCpiDedupStats stats;
    stats.input_count = detections.size();
    const MechanicalScanConfig& m = cfg.mechanical_scan;
    const bool overlapping = m.cpi_step_pulse < m.cpi_pulse_count;
    if (cfg.scan_mode != ScanMode::Mechanical || !m.enable_cpi_dedup ||
        !overlapping || detections.size() < 2U) {
        stats.output_count = stats.input_count;
        return stats;
    }

    std::vector<std::size_t> order(detections.size());
    std::iota(order.begin(), order.end(), 0U);
    const auto centerError = [](const GMTIOutput::DetectionCsvRecord& r) {
        return std::isfinite(r.theta_true_deg) && std::isfinite(r.az_center_deg)
            ? std::abs(r.theta_true_deg - r.az_center_deg)
            : std::numeric_limits<double>::infinity();
    };
    std::stable_sort(order.begin(), order.end(),
        [&detections, &centerError](std::size_t lhs, std::size_t rhs) {
            const double lhs_error = centerError(detections[lhs]);
            const double rhs_error = centerError(detections[rhs]);
            if (std::isfinite(lhs_error) != std::isfinite(rhs_error)) {
                return std::isfinite(lhs_error);
            }
            if (std::isfinite(lhs_error) && lhs_error != rhs_error) {
                return lhs_error < rhs_error;
            }
            const double lhs_amp = detections[lhs].amplitude;
            const double rhs_amp = detections[rhs].amplitude;
            const bool lhs_ok = std::isfinite(lhs_amp) && lhs_amp > 0.0;
            const bool rhs_ok = std::isfinite(rhs_amp) && rhs_amp > 0.0;
            if (lhs_ok != rhs_ok) return lhs_ok;
            return lhs_ok && rhs_ok ? lhs_amp > rhs_amp : false;
        });

    std::vector<bool> keep(detections.size(), true);
    for (std::size_t oi = 0; oi < order.size(); ++oi) {
        const std::size_t i = order[oi];
        if (!keep[i]) continue;
        const GMTIOutput::DetectionCsvRecord& retained = detections[i];
        if (retained.scan_id < 0 || retained.window_id < 0 ||
            !std::isfinite(retained.utc) || !std::isfinite(retained.e) ||
            !std::isfinite(retained.n) ||
            !std::isfinite(retained.radial_velocity_mps)) {
            continue;
        }
        for (std::size_t oj = oi + 1U; oj < order.size(); ++oj) {
            const std::size_t j = order[oj];
            if (!keep[j]) continue;
            const GMTIOutput::DetectionCsvRecord& candidate = detections[j];
            if (candidate.scan_id != retained.scan_id ||
                std::abs(candidate.window_id - retained.window_id) != 1 ||
                !std::isfinite(candidate.utc) || !std::isfinite(candidate.e) ||
                !std::isfinite(candidate.n) ||
                !std::isfinite(candidate.radial_velocity_mps)) {
                continue;
            }
            if (std::abs(candidate.utc - retained.utc) > m.dedup_time_gate_s ||
                std::hypot(candidate.e - retained.e, candidate.n - retained.n) >
                    m.dedup_position_gate_m ||
                std::abs(candidate.radial_velocity_mps -
                         retained.radial_velocity_mps) > m.dedup_velocity_gate_mps) {
                continue;
            }
            keep[j] = false;
            ++stats.removed_duplicate_count;
        }
    }

    if (stats.removed_duplicate_count > 0U) {
        std::vector<GMTIOutput::DetectionCsvRecord> retained_detections;
        std::vector<double> retained_mt;
        retained_detections.reserve(detections.size() - stats.removed_duplicate_count);
        retained_mt.reserve((detections.size() - stats.removed_duplicate_count) * 8U);
        for (std::size_t i = 0; i < detections.size(); ++i) {
            if (!keep[i]) continue;
            retained_detections.push_back(detections[i]);
            retained_mt.insert(retained_mt.end(),
                               mt_records.begin() + static_cast<std::ptrdiff_t>(i * 8U),
                               mt_records.begin() + static_cast<std::ptrdiff_t>((i + 1U) * 8U));
        }
        detections.swap(retained_detections);
        mt_records.swap(retained_mt);
    }
    stats.output_count = detections.size();
    return stats;
}
