#include "GMTIProcessor.hpp"
#include "trig_lut.hpp"


// 角度->弧度
static inline double deg2rad(double deg) { return deg * M_PI / 180.0; }

// 在已升序的 faAxis 上找与 val 最接近的下标（0-based）
// 若 faAxis 为空返回 -1
static inline int nearest_index(const std::vector<double>& faAxis, double val) {
    if (faAxis.empty()) return -1;
    auto it = std::lower_bound(faAxis.begin(), faAxis.end(), val);
    if (it == faAxis.begin()) return 0;
    if (it == faAxis.end())   return static_cast<int>(faAxis.size() - 1);
    // 夹在 prev(it) 与 it 之间，取更近者
    auto iR = static_cast<int>(it - faAxis.begin());
    auto iL = iR - 1;
    return (std::abs(faAxis[iL] - val) <= std::abs(faAxis[iR] - val)) ? iL : iR;
}

/**
 * 动态支撑域计算（inline）
 *
 * The nominal clutter support is the Doppler image of a conservative angular
 * support, not the ideal 3-dB beam edge.  The historical MATLAB/reference
 * implementation uses a 2-degree half-angle (`sind(2)`) as the support
 * envelope.  Keep that envelope for narrow configured beams, while never
 * making it narrower than half of the configured full beam width.  This is
 * intentional guard margin for pointing/model error and beam-edge leakage;
 * it must not be replaced by the exact +/- beamwidth/2 edge without a
 * separately calibrated uncertainty budget.
 *
 *   support_half_angle = max(2 deg, beamwidth / 2)
 *   BW_az = 2 * |plane.V| * sin(support_half_angle) / cfg.lambda
 *
 * For the supplied 15.8-GHz / 0.508-m azimuth aperture, the rectangular-
 * aperture first-null half-angle is asin(lambda / D) ~= 2.14 degrees.  The
 * 2-degree reference envelope is therefore a near-first-null clutter guard,
 * while `beamwidth_deg` remains the nominal 3-dB beam-width input.  The
 * 0.345-m dimension is the orthogonal aperture and is not used for azimuth
 * support unless the antenna installation defines that dimension as azimuth.
 *
 * The FFT-bin guard below is a second, independent discretization margin.
 *   fd_st = fa2 - BW_az; az_center = argmin|faAxis-fa2|;
 *   fd_ed = fa2 + BW_az; az_st/az_ed 同理
 *
 * 参数：
 *   faAxis   : 频率轴（长度 ~ pulse_num）
 *   fa2      : 多普勒中心
 *   plane    : 需包含 V (m/s)
 *   cfg      : 需包含 lambda, rg_st, rg_ed
 * 输出：
 *   az_center, az_st, az_ed : 方位索引(0-based)
 *   fd_st, fd_ed, BW_az     : 便于后续使用的频率量
 *   rg_st_out, rg_ed_out    : 直接透传 cfg 的距离支撑域（方便调用端）
 */
bool GMTIProcessor::computeDynamicSupportDomain(
        const std::vector<double>& faAxis,
        double fa2,
        const GMTIOutput::Plane& plane,
        const Config& cfg,
        int& az_center,
        int& az_st,
        int& az_ed,
        double& fd_st,
        double& fd_ed,
        double& BW_az,
        int& rg_st_out,
        int& rg_ed_out)
{
    if (faAxis.empty() || !(cfg.lambda > 0.0) ||
        !std::isfinite(cfg.lambda) || !std::isfinite(fa2) ||
        !std::isfinite(plane.V) ||
        !(cfg.beamwidth_deg > 0.0) || !std::isfinite(cfg.beamwidth_deg)) {
        return false;
    }

    for (std::size_t i = 1; i < faAxis.size(); ++i) {
        if (!std::isfinite(faAxis[i - 1]) ||
            !std::isfinite(faAxis[i]) || faAxis[i] <= faAxis[i - 1]) {
            return false;
        }
    }

    // 距离支撑域直接来自 cfg
    rg_st_out = cfg.rg_st;
    rg_ed_out = cfg.rg_ed;

    // Preserve the reference support margin.  For a wider configured beam,
    // the physical half beam takes precedence so the support cannot clip it.
    constexpr double kReferenceSupportHalfAngleDeg = 2.0;
    const double beam_half_deg = 0.5 * cfg.beamwidth_deg;
    const double support_half_deg = std::max(
        kReferenceSupportHalfAngleDeg, beam_half_deg);
    const double support_half_rad = deg2rad(support_half_deg);
    BW_az = 2.0 * std::abs(plane.V) *
            std::abs(gmti::trig_lut::sin(support_half_rad)) / cfg.lambda;

    // Map the continuous boundary to FFT rows conservatively by half a bin.
    // This can retain one boundary row, but never drops clutter whose Doppler
    // lies exactly on the visibility edge.
    double fd_bin = 0.0;
    if (faAxis.size() > 1U) {
        fd_bin = std::abs(faAxis[1] - faAxis[0]);
    }
    if (!(fd_bin > 0.0) && cfg.fd_res > 0.0) {
        fd_bin = cfg.fd_res;
    }
    const double boundary_guard_hz = 0.5 * std::max(0.0, fd_bin);

    // 动态上下边界频率
    fd_st = fa2 - BW_az - boundary_guard_hz;
    fd_ed = fa2 + BW_az + boundary_guard_hz;

    // 最近邻索引（0-based）。连续边界已经包含半个 FFT bin 的覆盖裕量。
    az_center = nearest_index(faAxis, fa2);
    az_st     = nearest_index(faAxis, fd_st);
    az_ed     = nearest_index(faAxis, fd_ed);

    // 容错：若任一未找到
    if (az_center < 0 || az_st < 0 || az_ed < 0) return false;

    return true;
}
