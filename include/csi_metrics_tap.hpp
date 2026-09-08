#ifndef CSI_METRICS_TAP_HPP
#define CSI_METRICS_TAP_HPP

#include <array>
#include <complex>
#include <string>
#include <vector>

struct Config;

namespace gmti {
namespace metrics {

// Writes an observational tap of the exact matrices used by the production
// CSI/CFAR path.  The function never changes samples or detection decisions.
bool writeCsiMetricsTap(const Config& cfg,
                        int period_id,
                        int beam_id,
                        int rows,
                        int cols,
                        int az_st,
                        int az_ed,
                        int rg_st,
                        int rg_ed,
                        const std::vector<float>& fa_axis_hz,
                        const std::vector<double>& range_axis_m,
                        const std::vector<std::complex<float> >& channel1,
                        const std::vector<std::complex<float> >& channel2,
                        const std::vector<std::complex<float> >& ctdr_channel1,
                        const std::vector<std::complex<float> >& ctdr_channel2,
                        const std::vector<std::complex<float> >& csi_after,
                        const std::array<float, 2>& csi_phase_fit,
                        std::string& error);

} // namespace metrics
} // namespace gmti

#endif // CSI_METRICS_TAP_HPP
