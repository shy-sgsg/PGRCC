// The production CSI entry point is intentionally private.  This focused
// selftest exposes it only in this translation unit so the CPU H0 fallback can
// be checked against the same inputs used by the CUDA regression.
#include <bits/stdc++.h>

#define private public
#include "GMTIProcessor.hpp"
#undef private

void GMTIProcessor::cleanupCUDAResources() {}

namespace {

struct ProbeResult {
    bool ok = false;
    std::vector<std::complex<float>> output;
};

ProbeResult runProbe(GMTIProcessor* processor, bool gate, bool coherent) {
    Config cfg;
    cfg.pulse_num = 4;
    cfg.rg_len = 8;
    cfg.csi_row_coherence_gate_enable = gate;
    cfg.csi_row_coherence_min = 0.5;
    cfg.csi_cancellation_mode = "legacy_min_magnitude";
    cfg.csi_bypass_enable = false;
    cfg.csi_metrics_enable = false;
    cfg.runtime_diagnostics_enabled = false;
    cfg.p38_csi_override_k_rad_per_hz = 0.0;
    cfg.p38_csi_override_b_rad = 0.0;

    const std::size_t total = static_cast<std::size_t>(cfg.pulse_num * cfg.rg_len);
    std::vector<float> fa(static_cast<std::size_t>(cfg.pulse_num), 0.0f);
    std::vector<std::complex<float>> f1(total), f2(total);
    for (int row = 0; row < cfg.pulse_num; ++row) {
        for (int col = 0; col < cfg.rg_len; ++col) {
            const std::size_t index = static_cast<std::size_t>(row * cfg.rg_len + col);
            f1[index] = std::complex<float>(1.0f, 0.0f);
            if (coherent) {
                f2[index] = std::complex<float>(0.5f, 0.0f);
            } else {
                f2[index] = std::complex<float>(
                    (col % 2) == 0 ? 1.0f : -1.0f, 0.0f);
            }
        }
    }

    std::vector<std::complex<float>> output;
    std::array<float, 2> p38{};
    std::vector<float> phase_trace;
    std::vector<float> row_fa;
    const bool ok = processor->clutter_cancel_38_paper_1(
        fa, f1, f2, 0, 0, cfg.pulse_num - 1, cfg.rg_len - 1,
        cfg, output, p38, phase_trace, row_fa, nullptr);
    ProbeResult result;
    result.ok = ok;
    result.output = std::move(output);
    return result;
}

float maxDifference(const std::vector<std::complex<float>>& lhs,
                    const std::vector<std::complex<float>>& rhs) {
    float result = 0.0f;
    for (std::size_t i = 0; i < lhs.size(); ++i) {
        result = std::max(result, std::abs(lhs[i] - rhs[i]));
    }
    return result;
}

}  // namespace

int main() {
    // Do not destroy the processor: this focused target supplies no production
    // CUDA cleanup object, and the OS reclaims the stream on process exit.
    GMTIProcessor* processor = new GMTIProcessor();
    const auto low_gate = runProbe(processor, true, false);
    const auto low_legacy = runProbe(processor, false, false);
    const auto coherent_gate = runProbe(processor, true, true);
    const auto coherent_legacy = runProbe(processor, false, true);

    const std::vector<std::complex<float>> f1(
        32, std::complex<float>(1.0f, 0.0f));
    const float low_bypass_error = maxDifference(low_gate.output, f1);
    const float low_legacy_error = maxDifference(low_legacy.output, f1);
    const float coherent_gate_difference = maxDifference(
        coherent_gate.output, coherent_legacy.output);
    const bool pass = low_gate.ok && low_legacy.ok && coherent_gate.ok &&
                      coherent_legacy.ok && low_bypass_error < 1.0e-6f &&
                      low_legacy_error > 0.5f &&
                      coherent_gate_difference < 1.0e-6f;

    std::cout << "cpu_gate_probe pass=" << (pass ? 1 : 0)
              << " low_gate_error=" << low_bypass_error
              << " low_legacy_error=" << low_legacy_error
              << " coherent_gate_difference=" << coherent_gate_difference
              << std::endl;
    return pass ? 0 : 1;
}
