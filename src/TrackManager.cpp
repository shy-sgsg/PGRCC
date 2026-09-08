#include "TrackManager.hpp"
#include "geo/geoProj.hpp"
#include "runtime_diagnostics.hpp"
#include "track_output_state_source.hpp"
#include "trig_lut.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <sstream>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace {

constexpr double kInvalidCost = 1.0e12;
constexpr double kMinMeasurementStdM = 20.0;
constexpr double kProcessAccelMps2 = 5.0;
constexpr double kPi = 3.14159265358979323846;
constexpr double kRadToDeg = 180.0 / kPi;
constexpr double kNumericEpsilon = 1.0e-9;
constexpr double kSmallDistanceM = 1.0;
constexpr double kSmallSpeedMps = 0.5;

enum class TrackDistanceMode {
    Euclidean = 0,
    MahalanobisSquared = 1
};

enum class TrackAssignmentMode {
    GreedyNearestNeighbor = 0,
    Hungarian = 1
};

using TrackOutputStateSource = gmti::tracking::TrackOutputStateSource;

struct DetectionPoint {
    GMTIDetection det;
    double e = 0.0;
    double n = 0.0;
};

struct AssocScore {
    bool valid = false;
    double total_cost = kInvalidCost;
    double euclidean_dist_m = 0.0;
    double mahalanobis_d2 = kInvalidCost;
    double instant_speed_mps = 0.0;
    double speed_diff_mps = 0.0;
    double heading_diff_deg = 0.0;
    double detection_speed_diff_mps = 0.0;
    double distance_cost = 0.0;
    double speed_cost = 0.0;
    double heading_cost = 0.0;
    double detection_speed_cost = 0.0;
    bool speed_reference_available = false;
    bool heading_reference_available = false;
    bool detection_speed_available = false;

    enum class RejectReason {
        None,
        EuclideanGate,
        MahalanobisGate,
        MaxSpeedGate,
        HeadingGate,
        DetectionSpeedGate,
        InvalidCovariance,
        InvalidNumber
    };

    RejectReason reject_reason = RejectReason::None;
};

static double safeNonnegative(double value)
{
    return std::isfinite(value) ? std::max(0.0, value) : 0.0;
}

static TrackOutputStateSource normalizedTrackOutputStateSource(const Config& cfg)
{
    std::string canonical;
    if (gmti::tracking::canonicalizeTrackOutputStateSource(
            cfg.track_output_state_source, canonical)) {
        return gmti::tracking::trackOutputStateSourceFromCanonical(canonical);
    }
    static bool warned = false;
    if (!warned) {
        std::cerr << "[TRACK][WARN] invalid track_output_state_source=\""
                  << cfg.track_output_state_source
                  << "\", fail-safe fallback to measurement." << std::endl;
        warned = true;
    }
    return TrackOutputStateSource::Measurement;
}

static const char* outputStateSourceName(TrackOutputStateSource source)
{
    return gmti::tracking::trackOutputStateSourceName(source);
}

static int normalizedDistanceMode(const Config& cfg)
{
    if (cfg.track_distance_mode == 0 || cfg.track_distance_mode == 1) {
        return cfg.track_distance_mode;
    }
    static bool warned = false;
    if (!warned) {
        std::cerr << "[TRACK][WARN] invalid track_distance_mode="
                  << cfg.track_distance_mode << ", fallback to 1 (MahalanobisSquared)."
                  << std::endl;
        warned = true;
    }
    return 1;
}

static int normalizedAssignmentMode(const Config& cfg)
{
    if (cfg.track_assignment_mode == 0 || cfg.track_assignment_mode == 1) {
        return cfg.track_assignment_mode;
    }
    static bool warned = false;
    if (!warned) {
        std::cerr << "[TRACK][WARN] invalid track_assignment_mode="
                  << cfg.track_assignment_mode
                  << ", fallback to 0 (GreedyNearestNeighbor)."
                  << std::endl;
        warned = true;
    }
    return 0;
}

static const char* distanceModeName(int mode)
{
    return mode == static_cast<int>(TrackDistanceMode::Euclidean)
        ? "Euclidean" : "MahalanobisSquared";
}

static const char* assignmentModeName(int mode)
{
    return mode == static_cast<int>(TrackAssignmentMode::GreedyNearestNeighbor)
        ? "GreedyNearestNeighbor" : "Hungarian";
}

static const char* rejectReasonName(AssocScore::RejectReason reason)
{
    switch (reason) {
    case AssocScore::RejectReason::None: return "None";
    case AssocScore::RejectReason::EuclideanGate: return "EuclideanGate";
    case AssocScore::RejectReason::MahalanobisGate: return "MahalanobisGate";
    case AssocScore::RejectReason::MaxSpeedGate: return "MaxSpeedGate";
    case AssocScore::RejectReason::HeadingGate: return "HeadingGate";
    case AssocScore::RejectReason::DetectionSpeedGate: return "DetectionSpeedGate";
    case AssocScore::RejectReason::InvalidCovariance: return "InvalidCovariance";
    case AssocScore::RejectReason::InvalidNumber: return "InvalidNumber";
    }
    return "Unknown";
}

static bool costBeatsDummy(double cost, double dummy_cost, bool allow_equal)
{
    return std::isfinite(cost) &&
        (allow_equal ? cost <= dummy_cost : cost < dummy_cost);
}

static bool validUtc(double utc)
{
    return std::isfinite(utc) && utc > 0.0;
}

static double snapshotUtc(const ManagedTrack& tr, double frame_utc, double dt)
{
    const double state_utc = validUtc(tr.state_utc) ? tr.state_utc : tr.utc;
    if (validUtc(frame_utc) &&
        (!validUtc(state_utc) || frame_utc >= state_utc)) {
        return frame_utc;
    }
    if (validUtc(state_utc)) {
        return state_utc + std::max(0.0, dt);
    }
    return 0.0;
}

static void captureKinematicSnapshot(TrackKinematicSnapshot& snapshot,
                                     const ManagedTrack& tr,
                                     double utc,
                                     double range,
                                     double direction)
{
    snapshot.valid = std::isfinite(tr.e) && std::isfinite(tr.n) &&
                     std::isfinite(tr.ve) && std::isfinite(tr.vn);
    snapshot.e = tr.e;
    snapshot.n = tr.n;
    snapshot.ve = tr.ve;
    snapshot.vn = tr.vn;
    snapshot.utc = utc;
    snapshot.range = range;
    snapshot.direction = direction;
}

static double currentUtc(const std::vector<DetectionPoint>& detections)
{
    for (const auto& det : detections) {
        if (validUtc(det.det.utcMid)) {
            return det.det.utcMid;
        }
    }
    return 0.0;
}

static double safeDefaultDt(const Config& cfg)
{
    return std::max(1e-3, cfg.track_default_dt);
}

static double frameDtLegacy(const ManagedTrack& tr, double utc, int result_id)
{
    if (validUtc(utc) && validUtc(tr.utc)) {
        const double dt = utc - tr.utc;
        if (dt > 1e-3) {
            return dt;
        }
    }
    if (result_id > 0 && tr.last_result_id > 0) {
        return static_cast<double>(std::max(1, result_id - tr.last_result_id));
    }
    return 1.0;
}

static double computeDtForPredict(const Config& cfg, const ManagedTrack& tr, double frame_utc)
{
    const double state_utc = validUtc(tr.state_utc) ? tr.state_utc : tr.utc;
    if (validUtc(frame_utc) && validUtc(state_utc)) {
        // A repeated/non-monotonic frame UTC is not forward elapsed time.  In
        // particular, do not integrate velocity again after an already-coasted
        // state merely because no new measurement arrived.
        return std::max(0.0, frame_utc - state_utc);
    }
    return safeDefaultDt(cfg);
}

static double computeDtForDetection(const Config& cfg,
                                    const ManagedTrack& tr,
                                    const GMTIDetection& det,
                                    double frame_utc)
{
    if (validUtc(det.utcMid) && validUtc(tr.utc) && det.utcMid > tr.utc) {
        return det.utcMid - tr.utc;
    }
    if (validUtc(frame_utc) && validUtc(tr.utc) && frame_utc > tr.utc) {
        return frame_utc - tr.utc;
    }
    return safeDefaultDt(cfg);
}

static void initCovarianceLegacy(ManagedTrack& tr, double gate_m)
{
    tr.P.fill(0.0);
    const double pos_var = std::max(kMinMeasurementStdM, gate_m * 0.5);
    const double vel_std = std::max(10.0, gate_m * 0.05);
    tr.P[0] = pos_var * pos_var;
    tr.P[5] = pos_var * pos_var;
    tr.P[10] = vel_std * vel_std;
    tr.P[15] = vel_std * vel_std;
}

static void initCovarianceOnline(ManagedTrack& tr)
{
    tr.P.fill(0.0);
    tr.P[0] = 100.0;
    tr.P[5] = 100.0;
    tr.P[10] = 1000.0;
    tr.P[15] = 1000.0;
}

static void kalmanPredict(ManagedTrack& tr, const Config& cfg, double dt)
{
    dt = std::max(1e-3, dt);
    tr.e += tr.ve * dt;
    tr.n += tr.vn * dt;

    const double p00 = tr.P[0],  p01 = tr.P[1],  p02 = tr.P[2],  p03 = tr.P[3];
    const double p10 = tr.P[4],  p11 = tr.P[5],  p12 = tr.P[6],  p13 = tr.P[7];
    const double p20 = tr.P[8],  p21 = tr.P[9],  p22 = tr.P[10], p23 = tr.P[11];
    const double p30 = tr.P[12], p31 = tr.P[13], p32 = tr.P[14], p33 = tr.P[15];

    tr.P[0] = p00 + dt * (p20 + p02) + dt * dt * p22 + cfg.track_process_noise_pos;
    tr.P[1] = p01 + dt * (p21 + p03) + dt * dt * p23;
    tr.P[2] = p02 + dt * p22;
    tr.P[3] = p03 + dt * p23;

    tr.P[4] = p10 + dt * (p30 + p12) + dt * dt * p32;
    tr.P[5] = p11 + dt * (p31 + p13) + dt * dt * p33 + cfg.track_process_noise_pos;
    tr.P[6] = p12 + dt * p32;
    tr.P[7] = p13 + dt * p33;

    tr.P[8] = p20 + dt * p22;
    tr.P[9] = p21 + dt * p23;
    tr.P[10] = p22 + cfg.track_process_noise_vel;
    tr.P[11] = p23;

    tr.P[12] = p30 + dt * p32;
    tr.P[13] = p31 + dt * p33;
    tr.P[14] = p32;
    tr.P[15] = p33 + cfg.track_process_noise_vel;
}

static void predictTrackLegacy(ManagedTrack& tr, double dt)
{
    Config cfg;
    cfg.track_process_noise_pos = 0.25 * dt * dt * dt * dt * kProcessAccelMps2 * kProcessAccelMps2;
    cfg.track_process_noise_vel = dt * dt * kProcessAccelMps2 * kProcessAccelMps2;
    kalmanPredict(tr, cfg, dt);
}

static double mahalanobis2(const ManagedTrack& tr,
                           double det_e,
                           double det_n,
                           double measurement_var,
                           double& dist_out)
{
    const double rx = det_e - tr.e;
    const double ry = det_n - tr.n;
    dist_out = std::sqrt(rx * rx + ry * ry);

    const double s00 = tr.P[0] + measurement_var;
    const double s01 = tr.P[1];
    const double s10 = tr.P[4];
    const double s11 = tr.P[5] + measurement_var;
    const double det_s = s00 * s11 - s01 * s10;
    if (!std::isfinite(rx) || !std::isfinite(ry) || !std::isfinite(dist_out) ||
        !std::isfinite(s00) || !std::isfinite(s01) ||
        !std::isfinite(s10) || !std::isfinite(s11) ||
        !std::isfinite(det_s) || det_s <= kNumericEpsilon) {
        return kInvalidCost;
    }

    const double inv00 = s11 / det_s;
    const double inv01 = -s01 / det_s;
    const double inv10 = -s10 / det_s;
    const double inv11 = s00 / det_s;
    const double d2 =
        rx * (inv00 * rx + inv01 * ry) + ry * (inv10 * rx + inv11 * ry);
    if (!std::isfinite(d2) || d2 < 0.0) {
        return kInvalidCost;
    }
    return d2;
}

static void kalmanUpdate(ManagedTrack& tr,
                         double det_e,
                         double det_n,
                         double measurement_var,
                         double* residual_d2)
{
    const double y0 = det_e - tr.e;
    const double y1 = det_n - tr.n;

    const double s00 = tr.P[0] + measurement_var;
    const double s01 = tr.P[1];
    const double s10 = tr.P[4];
    const double s11 = tr.P[5] + measurement_var;
    const double det_s = s00 * s11 - s01 * s10;
    if (std::fabs(det_s) < 1e-9) {
        tr.e = det_e;
        tr.n = det_n;
        if (residual_d2) {
            *residual_d2 = 0.0;
        }
        return;
    }

    const double inv00 = s11 / det_s;
    const double inv01 = -s01 / det_s;
    const double inv10 = -s10 / det_s;
    const double inv11 = s00 / det_s;
    if (residual_d2) {
        *residual_d2 = y0 * (inv00 * y0 + inv01 * y1) + y1 * (inv10 * y0 + inv11 * y1);
    }

    double K[8] = {0.0};
    for (int r = 0; r < 4; ++r) {
        const double p_r0 = tr.P[r * 4 + 0];
        const double p_r1 = tr.P[r * 4 + 1];
        K[r * 2 + 0] = p_r0 * inv00 + p_r1 * inv10;
        K[r * 2 + 1] = p_r0 * inv01 + p_r1 * inv11;
    }

    tr.e += K[0] * y0 + K[1] * y1;
    tr.n += K[2] * y0 + K[3] * y1;
    tr.ve += K[4] * y0 + K[5] * y1;
    tr.vn += K[6] * y0 + K[7] * y1;

    double oldP[16];
    for (int i = 0; i < 16; ++i) {
        oldP[i] = tr.P[i];
    }
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            tr.P[r * 4 + c] = oldP[r * 4 + c]
                - K[r * 2 + 0] * oldP[c]
                - K[r * 2 + 1] * oldP[4 + c];
        }
    }
}

static double median(std::deque<double> values)
{
    if (values.empty()) {
        return 0.0;
    }
    std::sort(values.begin(), values.end());
    const size_t mid = values.size() / 2;
    if (values.size() % 2 == 0) {
        return 0.5 * (values[mid - 1] + values[mid]);
    }
    return values[mid];
}

static double absAngleDiff(double a, double b)
{
    double d = std::fmod(a - b + kPi, 2.0 * kPi);
    if (d < 0.0) {
        d += 2.0 * kPi;
    }
    return std::fabs(d - kPi);
}

static double circularMean(const std::deque<double>& angles)
{
    if (angles.empty()) {
        return 0.0;
    }
    double s = 0.0;
    double c = 0.0;
    for (double a : angles) {
        s += gmti::trig_lut::sin(a);
        c += gmti::trig_lut::cos(a);
    }
    return gmti::trig_lut::atan2(s, c);
}

static void pushBounded(std::deque<int>& values, int value, int window)
{
    values.push_back(value);
    while (static_cast<int>(values.size()) > std::max(1, window)) {
        values.pop_front();
    }
}

static void pushBounded(std::deque<double>& values, double value, int window)
{
    if (!std::isfinite(value)) {
        return;
    }
    values.push_back(value);
    while (static_cast<int>(values.size()) > std::max(1, window)) {
        values.pop_front();
    }
}

static void pushPointHistory(ManagedTrack& tr, double e, double n, double utc, int result_id, const Config& cfg)
{
    tr.point_history.push_back(TrackPoint{e, n, utc, result_id});
    const int keep = std::max(3, cfg.track_linearity_window);
    while (static_cast<int>(tr.point_history.size()) > keep) {
        tr.point_history.pop_front();
    }
}

static int recentHits(const ManagedTrack& tr, int window)
{
    int count = 0;
    const int n = std::min<int>(std::max(1, window), tr.hit_history.size());
    for (int i = 0; i < n; ++i) {
        count += tr.hit_history[tr.hit_history.size() - 1 - i];
    }
    return count;
}

static double distanceEN(double e0, double n0, double e1, double n1)
{
    const double de = e1 - e0;
    const double dn = n1 - n0;
    return std::sqrt(de * de + dn * dn);
}

static double computeLinearity(const ManagedTrack& tr, int window)
{
    const int n = std::min<int>(std::max(3, window), tr.point_history.size());
    if (n < 3) {
        return 1.0;
    }
    const int start = static_cast<int>(tr.point_history.size()) - n;
    const TrackPoint& first = tr.point_history[start];
    const TrackPoint& last = tr.point_history.back();
    const double straight = distanceEN(first.e, first.n, last.e, last.n);
    double path = 0.0;
    for (int i = start + 1; i < static_cast<int>(tr.point_history.size()); ++i) {
        path += distanceEN(tr.point_history[i - 1].e, tr.point_history[i - 1].n,
                           tr.point_history[i].e, tr.point_history[i].n);
    }
    if (path <= 1e-6) {
        return 1.0;
    }
    return straight / path;
}

static bool linearityOK(const ManagedTrack& tr, const Config& cfg)
{
    if (tr.point_history.size() < 3) {
        return true;
    }
    return computeLinearity(tr, cfg.track_linearity_window) >= cfg.track_min_linearity_confirm;
}

static bool shouldPromoteToConfirmed(const Config& cfg, const ManagedTrack& tr)
{
    if (recentHits(tr, cfg.track_confirm_window) < std::max(1, cfg.track_confirm_hits)) {
        return false;
    }
    return linearityOK(tr, cfg);
}

static bool shouldOutputThisFrame(const Config& cfg, const ManagedTrack& tr, int result_id)
{
    if (tr.state != TrackState::Confirmed) {
        return false;
    }
    if (!tr.matched_this_frame) {
        return false;
    }
    if (tr.last_result_id != result_id) {
        return false;
    }
    return recentHits(tr, cfg.track_confirm_window) >= std::max(1, cfg.track_confirm_hits);
}

static bool buildProtocolDetectionForFrame(const Config& cfg,
                                           const ManagedTrack& tr,
                                           int result_id,
                                           const std::vector<GMTIDetection>& current_detections,
                                           GMTIDetection& output,
                                           std::string* failure_reason = nullptr)
{
    const auto fail = [&](const char* reason) {
        if (failure_reason) {
            *failure_reason = reason;
        }
        return false;
    };
    if (!shouldOutputThisFrame(cfg, tr, result_id)) {
        return fail("track is not eligible for current-period output");
    }
    if (tr.matched_det_index < 0 ||
        tr.matched_det_index >= static_cast<int>(current_detections.size())) {
        return fail("matched detection index is invalid");
    }

    const TrackOutputStateSource source = normalizedTrackOutputStateSource(cfg);
    if (source == TrackOutputStateSource::Measurement) {
        // Default/protocol-compatible mode: the point observables are the exact
        // detection associated in this result period.  Speed remains the
        // existing inter-period track estimate because detector files currently
        // store speed=0; this exception is explicit in the audit CSV/docs.
        output = current_detections[static_cast<size_t>(tr.matched_det_index)];
        output.id = tr.id;
        output.speed = tr.speed;
        return true;
    }

    const TrackKinematicSnapshot* snapshot = nullptr;
    if (source == TrackOutputStateSource::Prediction) {
        snapshot = &tr.prediction_snapshot;
    } else {
        snapshot = &tr.filtered_snapshot;
    }
    if (!snapshot->valid) {
        return fail(source == TrackOutputStateSource::Prediction
                        ? "prediction snapshot is unavailable"
                        : "Kalman filtered snapshot is unavailable");
    }
    if (!std::isfinite(snapshot->range) || !std::isfinite(snapshot->direction) ||
        !std::isfinite(snapshot->utc)) {
        return fail("selected state contains a non-finite ancillary field");
    }

    // Do not seed from current_detections here: prediction mode must not
    // accidentally leak current measurement fields into its payload.
    output = GMTIDetection{};
    output.id = tr.id;
    output.e = snapshot->e;
    output.n = snapshot->n;
    // The two opt-in state modes expose a coherent [E,N,vE,vN] snapshot.
    // Measurement mode keeps the legacy multi-period displacement speed because
    // the detector speed field is currently zero; prediction/filtered modes use
    // the velocity that belongs to the selected Kalman snapshot.
    output.speed = std::hypot(snapshot->ve, snapshot->vn);
    output.direction = snapshot->direction;
    output.range = snapshot->range;
    output.utcMid = snapshot->utc;
    if (!Gaussp3RV(output.e, output.n, cfg.L0, output.lat, output.lon)) {
        return fail("selected EN state cannot be projected to latitude/longitude");
    }
    return true;
}

static const char* stateName(TrackState state)
{
    switch (state) {
    case TrackState::Tentative:
        return "Tentative";
    case TrackState::Confirmed:
        return "Confirmed";
    case TrackState::Coasted:
        return "Coasted";
    case TrackState::Deleted:
        return "Deleted";
    }
    return "Unknown";
}

static bool ensureDirectory(const std::string& path);
static std::ofstream openCsvAppend(const std::string& path, const char* header);

static std::string csvEscapeTrack(const std::string& value)
{
    bool quote = false;
    for (char ch : value) {
        if (ch == ',' || ch == '"' || ch == '\n' || ch == '\r') {
            quote = true;
            break;
        }
    }
    if (!quote) {
        return value;
    }
    std::string out = "\"";
    for (char ch : value) {
        if (ch == '"') {
            out += "\"\"";
        } else {
            out.push_back(ch);
        }
    }
    out.push_back('"');
    return out;
}

static std::string trackEvalDir(const Config& cfg)
{
    if (!cfg.track_debug_dir.empty()) {
        return cfg.track_debug_dir;
    }
    return cfg.result_add.empty() ? "." : cfg.result_add;
}

static std::string trackResultIdString(int result_id)
{
    if (result_id > 0) {
        std::ostringstream oss;
        oss << "GMTI" << std::setw(2) << std::setfill('0') << result_id;
        return oss.str();
    }
    return gmti::runtime::resultId();
}

static std::string trackCaseId()
{
    const std::string& v = gmti::runtime::caseId();
    return v.empty() ? "unknown" : v;
}

static std::string trackRunId()
{
    const std::string& v = gmti::runtime::runId();
    return v.empty() ? "unknown" : v;
}

static int trackScanPeriodId(int result_id)
{
    // main.cpp/pipe update TrackManager once per complete scan result after all
    // beams are merged.  The result id passed to updateRawDetections is therefore
    // the observable scan-period id; writing a constant zero makes time-series
    // truth matching, ID-switch and fragmentation metrics impossible.
    return result_id > 0 ? result_id : 0;
}

static bool detailedTrackDiagnosticsEnabled(const Config& cfg)
{
    // 最小航迹 CSV 是 release 文件/共享内存功能审计的一部分；全局诊断
    // 关闭时仍可由 track_debug_dump 显式请求，避免同时落盘其他运行时诊断。
    return cfg.runtime_diagnostics_enabled || cfg.track_debug_dump;
}

static void appendTrackEvent(const Config& cfg,
                             int result_id,
                             const ManagedTrack& tr,
                             const std::string& event,
                             int det_id,
                             const std::string& detail)
{
    if (!detailedTrackDiagnosticsEnabled(cfg) || cfg.track_debug_level < 1) {
        return;
    }
    const std::string dir = trackEvalDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }
    std::ofstream os = openCsvAppend(
        dir + "/track_events.csv",
        "case_id,run_id,result_id,period_id,track_id,event,state,det_id,utc,e,n,age,hit_count,miss_count,consecutive_hits,detail");
    if (!os.is_open()) {
        return;
    }
    os << std::setprecision(12)
       << csvEscapeTrack(trackCaseId()) << ","
       << csvEscapeTrack(trackRunId()) << ","
       << csvEscapeTrack(trackResultIdString(result_id)) << ","
       << trackScanPeriodId(result_id) << ","
       << tr.id << ","
       << csvEscapeTrack(event) << ","
       << stateName(tr.state) << ","
       << det_id << ","
       << tr.utc << ","
       << std::setprecision(6) << tr.e << ","
       << tr.n << ","
       << tr.age << ","
       << tr.hit_count << ","
       << tr.miss_count << ","
       << tr.consecutive_hits << ","
       << csvEscapeTrack(detail) << "\n";
}

static void appendTrackAssociationLog(const Config& cfg,
                                      int result_id,
                                      const std::vector<int>& active_tracks,
                                      const std::vector<ManagedTrack>& tracks,
                                      const std::vector<GMTIDetection>& dets,
                                      const std::vector<std::vector<AssocScore>>& scores,
                                      const std::vector<int>& assignment,
                                      double dummy_cost,
                                      bool allow_equal_dummy_cost)
{
    if (!detailedTrackDiagnosticsEnabled(cfg) || cfg.track_debug_level < 2) {
        return;
    }
    const std::string dir = trackEvalDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }
    std::ofstream os = openCsvAppend(
        dir + "/track_association_log.csv",
        "period_id,track_id,det_id,cost,gate_passed,gate_reason,assigned,distance_m,mahalanobis_distance,innovation_e,innovation_n");
    if (!os.is_open()) {
        return;
    }
    for (int row = 0; row < static_cast<int>(active_tracks.size()); ++row) {
        const int ti = active_tracks[row];
        if (ti < 0 || ti >= static_cast<int>(tracks.size())) {
            continue;
        }
        const ManagedTrack& tr = tracks[ti];
        for (int di = 0; di < static_cast<int>(dets.size()); ++di) {
            const AssocScore& score = scores[row][di];
            const bool assigned = row < static_cast<int>(assignment.size()) &&
                                  assignment[row] == di &&
                                  score.valid &&
                                  costBeatsDummy(score.total_cost, dummy_cost,
                                                 allow_equal_dummy_cost);
            os << std::setprecision(12)
               << trackScanPeriodId(result_id) << ","
               << tr.id << ","
               << di << ","
               << score.total_cost << ","
               << (score.valid ? 1 : 0) << ","
               << rejectReasonName(score.reject_reason) << ","
               << (assigned ? 1 : 0) << ","
               << score.euclidean_dist_m << ","
               << std::sqrt(std::max(0.0, score.mahalanobis_d2)) << ","
               << (dets[di].e - tr.e) << ","
               << (dets[di].n - tr.n) << "\n";
        }
    }
}

static const char* outputPositionSource(TrackOutputStateSource source)
{
    if (source == TrackOutputStateSource::Measurement) return "matched_detection";
    if (source == TrackOutputStateSource::Prediction) return "kalman_prior";
    return "kalman_posterior";
}

static const char* outputSpeedSource(TrackOutputStateSource source)
{
    if (source == TrackOutputStateSource::Measurement) {
        return "track_displacement_over_elapsed_time";
    }
    if (source == TrackOutputStateSource::Prediction) {
        return "kalman_prior_velocity";
    }
    return "kalman_posterior_velocity";
}

static const char* outputAncillarySource(TrackOutputStateSource source)
{
    return source == TrackOutputStateSource::Prediction
        ? "previous_detection_zero_order_hold"
        : "matched_detection";
}

static const char* outputTimeSource(TrackOutputStateSource source)
{
    return source == TrackOutputStateSource::Prediction
        ? "prediction_epoch"
        : "matched_detection";
}

static void appendTrackOutputPayloadAudit(const Config& cfg,
                                          int result_id,
                                          const ManagedTrack& tr,
                                          const GMTIDetection& measurement,
                                          const GMTIDetection& output)
{
    if (!detailedTrackDiagnosticsEnabled(cfg)) {
        return;
    }
    const std::string dir = trackEvalDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }
    std::ofstream os = openCsvAppend(
        dir + "/track_output_payloads.csv",
        "case_id,run_id,result_id,track_id,configured_source,resolved_source,matched_det_index,position_source,speed_source,range_source,direction_source,time_source,measurement_utc,measurement_e,measurement_n,measurement_lat,measurement_lon,measurement_speed,measurement_range,measurement_direction,prediction_valid,prediction_utc,prediction_e,prediction_n,prediction_ve,prediction_vn,prediction_range,prediction_direction,filtered_valid,filtered_utc,filtered_e,filtered_n,filtered_ve,filtered_vn,filtered_range,filtered_direction,output_utc,output_e,output_n,output_lat,output_lon,output_speed,output_range,output_direction");
    if (!os.is_open()) {
        return;
    }
    const TrackOutputStateSource source = normalizedTrackOutputStateSource(cfg);
    os << std::setprecision(12)
       << csvEscapeTrack(trackCaseId()) << ","
       << csvEscapeTrack(trackRunId()) << ","
       << csvEscapeTrack(trackResultIdString(result_id)) << ","
       << tr.id << ","
       << csvEscapeTrack(cfg.track_output_state_source) << ","
       << outputStateSourceName(source) << ","
       << tr.matched_det_index << ","
       << outputPositionSource(source) << ","
       << outputSpeedSource(source) << ","
       << outputAncillarySource(source) << ","
       << outputAncillarySource(source) << ","
       << outputTimeSource(source) << ","
       << measurement.utcMid << ","
       << measurement.e << "," << measurement.n << ","
       << measurement.lat << "," << measurement.lon << ","
       << measurement.speed << "," << measurement.range << ","
       << measurement.direction << ","
       << (tr.prediction_snapshot.valid ? 1 : 0) << ","
       << tr.prediction_snapshot.utc << ","
       << tr.prediction_snapshot.e << "," << tr.prediction_snapshot.n << ","
       << tr.prediction_snapshot.ve << "," << tr.prediction_snapshot.vn << ","
       << tr.prediction_snapshot.range << ","
       << tr.prediction_snapshot.direction << ","
       << (tr.filtered_snapshot.valid ? 1 : 0) << ","
       << tr.filtered_snapshot.utc << ","
       << tr.filtered_snapshot.e << "," << tr.filtered_snapshot.n << ","
       << tr.filtered_snapshot.ve << "," << tr.filtered_snapshot.vn << ","
       << tr.filtered_snapshot.range << ","
       << tr.filtered_snapshot.direction << ","
       << output.utcMid << ","
       << output.e << "," << output.n << ","
       << output.lat << "," << output.lon << ","
       << output.speed << "," << output.range << ","
       << output.direction << "\n";
}

static void writeTrackResultsCsv(const Config& cfg,
                                 int result_id,
                                 const std::vector<ManagedTrack>& tracks)
{
    if (!detailedTrackDiagnosticsEnabled(cfg) || cfg.track_debug_level < 1) {
        return;
    }
    const std::string dir = trackEvalDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }
    std::ofstream os = openCsvAppend(
        dir + "/track_results.csv",
        "case_id,run_id,result_id,config_hash,data_hash,git_commit,random_seed,period_id,track_id,state,is_output,matched_det_id,utc,e,n,lat,lon,ve,vn,speed,heading,range,direction,age,hit_count,miss_count,consecutive_hits");
    if (!os.is_open()) {
        return;
    }
    for (const auto& tr : tracks) {
        double lat = 0.0;
        double lon = 0.0;
        Gaussp3RV(tr.e, tr.n, cfg.L0, lat, lon);
        const double heading = gmti::trig_lut::atan2(tr.vn, tr.ve) * kRadToDeg;
        os << std::setprecision(12)
           << csvEscapeTrack(trackCaseId()) << ","
           << csvEscapeTrack(trackRunId()) << ","
           << csvEscapeTrack(trackResultIdString(result_id)) << ","
           << ","  // config_hash: filled by evaluation report from run_manifest when available
           << ","  // data_hash
           << ","  // git_commit
           << ","  // random_seed
           << trackScanPeriodId(result_id) << ","
           << tr.id << ","
           << stateName(tr.state) << ","
           << (tr.is_output_this_frame ? 1 : 0) << ","
           << tr.matched_det_index << ","
           << tr.utc << ","
           << std::setprecision(6) << tr.e << ","
           << tr.n << ","
           << std::setprecision(12) << lat << ","
           << lon << ","
           << tr.ve << ","
           << tr.vn << ","
           << tr.speed << ","
           << heading << ","
           << tr.range << ","
           << tr.direction << ","
           << tr.age << ","
           << tr.hit_count << ","
           << tr.miss_count << ","
           << tr.consecutive_hits << "\n";
    }
}

static void ensureP3CsvHeaders(const Config& cfg)
{
    if (!detailedTrackDiagnosticsEnabled(cfg)) {
        return;
    }
    const std::string dir = trackEvalDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }
    if (cfg.track_debug_level >= 1) {
        std::ofstream os = openCsvAppend(
            dir + "/track_results.csv",
            "case_id,run_id,result_id,config_hash,data_hash,git_commit,random_seed,period_id,track_id,state,is_output,matched_det_id,utc,e,n,lat,lon,ve,vn,speed,heading,range,direction,age,hit_count,miss_count,consecutive_hits");
    }
    if (cfg.track_debug_level >= 2) {
        std::ofstream os = openCsvAppend(
            dir + "/track_association_log.csv",
            "period_id,track_id,det_id,cost,gate_passed,gate_reason,assigned,distance_m,mahalanobis_distance,innovation_e,innovation_n");
    }
    if (cfg.track_debug_level >= 1) {
        std::ofstream os = openCsvAppend(
            dir + "/track_events.csv",
            "case_id,run_id,result_id,period_id,track_id,event,state,det_id,utc,e,n,age,hit_count,miss_count,consecutive_hits,detail");
    }
    {
        std::ofstream os = openCsvAppend(
            dir + "/track_association_accepts.csv",
            "result_id,track_id,track_state_before,det_index,last_measurement_utc,frame_utc,detection_utc,predicted_e,predicted_n,detection_e,detection_n,euclidean_dist_m,euclidean_gate_m,mahalanobis_d2,mahalanobis_gate_d2,instant_speed_mps,max_speed_gate_mps,total_cost,dummy_cost,gate_passed,cost_beats_dummy,post_assignment_accepted");
    }
    {
        std::ofstream os = openCsvAppend(
            dir + "/track_output_payloads.csv",
            "case_id,run_id,result_id,track_id,configured_source,resolved_source,matched_det_index,position_source,speed_source,range_source,direction_source,time_source,measurement_utc,measurement_e,measurement_n,measurement_lat,measurement_lon,measurement_speed,measurement_range,measurement_direction,prediction_valid,prediction_utc,prediction_e,prediction_n,prediction_ve,prediction_vn,prediction_range,prediction_direction,filtered_valid,filtered_utc,filtered_e,filtered_n,filtered_ve,filtered_vn,filtered_range,filtered_direction,output_utc,output_e,output_n,output_lat,output_lon,output_speed,output_range,output_direction");
    }
}

static void updateMotionHistoryFromEN(ManagedTrack& tr,
                                      const GMTIDetection& det,
                                      double dt,
                                      const Config& cfg)
{
    if (!tr.point_history.empty()) {
        // Use the full retained measurement history instead of copying the
        // detector's single-period radial-speed diagnostic or differentiating
        // only the noisiest last segment.  Timestamps are preferred so missed
        // periods and nonuniform scan intervals are handled correctly.
        const TrackPoint& first = tr.point_history.front();
        double elapsed = dt * static_cast<double>(tr.point_history.size());
        if (validUtc(det.utcMid) && validUtc(first.utc) && det.utcMid > first.utc) {
            elapsed = det.utcMid - first.utc;
        }
        elapsed = std::max(1e-3, elapsed);
        const double de = det.e - first.e;
        const double dn = det.n - first.n;
        const double dist = std::sqrt(de * de + dn * dn);
        tr.ve = de / elapsed;
        tr.vn = dn / elapsed;
        tr.speed = std::sqrt(tr.ve * tr.ve + tr.vn * tr.vn);
        pushBounded(tr.speed_history, tr.speed, cfg.track_linearity_window);
        const double heading_min_displacement = std::max(
            kSmallDistanceM,
            safeNonnegative(cfg.track_heading_min_displacement_m));
        if (dist >= heading_min_displacement && tr.speed >= kSmallSpeedMps) {
            pushBounded(tr.heading_history, gmti::trig_lut::atan2(dn, de), cfg.track_linearity_window);
        }
    }
}

static AssocScore computeAssocScoreEN(const Config& cfg,
                                      const ManagedTrack& tr,
                                      double det_e,
                                      double det_n,
                                      double det_utc,
                                      double det_speed,
                                      double frame_utc,
                                      double measurement_var)
{
    AssocScore score;
    if (!std::isfinite(det_e) || !std::isfinite(det_n) ||
        !std::isfinite(tr.e) || !std::isfinite(tr.n) ||
        !std::isfinite(measurement_var) || measurement_var < 0.0) {
        score.reject_reason = AssocScore::RejectReason::InvalidNumber;
        return score;
    }

    score.mahalanobis_d2 =
        mahalanobis2(tr, det_e, det_n, measurement_var, score.euclidean_dist_m);
    const bool mahalanobis_available =
        std::isfinite(score.mahalanobis_d2) && score.mahalanobis_d2 < kInvalidCost;

    double effective_gate_m = safeNonnegative(cfg.track_gate_m);
    double effective_chi2_gate = safeNonnegative(cfg.track_chi2_gate);
    if (tr.state == TrackState::Tentative) {
        effective_gate_m *= std::max(1.0, safeNonnegative(cfg.track_tentative_gate_scale));
        effective_chi2_gate *= std::max(1.0, safeNonnegative(cfg.track_tentative_chi2_scale));
    }

    GMTIDetection det{};
    det.e = det_e;
    det.n = det_n;
    det.utcMid = det_utc;
    const double dt = computeDtForDetection(cfg, tr, det, frame_utc);
    const TrackPoint* last_hit = tr.point_history.empty() ? nullptr : &tr.point_history.back();
    const double motion_dist = last_hit
        ? distanceEN(last_hit->e, last_hit->n, det_e, det_n)
        : score.euclidean_dist_m;
    score.instant_speed_mps = motion_dist / std::max(1e-3, dt);

    const bool early_track = (tr.hit_count < 2 || tr.point_history.size() < 2);
    double reference_speed = 0.0;
    if (!early_track && tr.speed_history.size() >= 2) {
        reference_speed = median(tr.speed_history);
        if (std::isfinite(reference_speed) && reference_speed >= 0.0) {
            score.speed_reference_available = true;
            score.speed_diff_mps = std::fabs(score.instant_speed_mps - reference_speed);
        }
    }

    const double heading_min_displacement = std::max(
        kSmallDistanceM,
        safeNonnegative(cfg.track_heading_min_displacement_m));
    if (!early_track && last_hit && tr.heading_history.size() >= 2 &&
        motion_dist >= heading_min_displacement) {
        const double heading_inst = gmti::trig_lut::atan2(det_n - last_hit->n,
                                                         det_e - last_hit->e);
        const double heading_ref = circularMean(tr.heading_history);
        if (std::isfinite(heading_inst) && std::isfinite(heading_ref)) {
            score.heading_reference_available = true;
            score.heading_diff_deg = absAngleDiff(heading_inst, heading_ref) * kRadToDeg;
        }
    }

    score.detection_speed_available =
        score.speed_reference_available && std::isfinite(det_speed) && det_speed > 0.0;
    if (score.detection_speed_available) {
        score.detection_speed_diff_mps = std::fabs(det_speed - reference_speed);
    }

    if (!std::isfinite(score.euclidean_dist_m) ||
        !std::isfinite(score.instant_speed_mps) ||
        !std::isfinite(score.speed_diff_mps) ||
        !std::isfinite(score.heading_diff_deg) ||
        !std::isfinite(score.detection_speed_diff_mps)) {
        score.reject_reason = AssocScore::RejectReason::InvalidNumber;
        return score;
    }

    if (cfg.track_use_euclidean_gate && effective_gate_m > 0.0 &&
        score.euclidean_dist_m > effective_gate_m) {
        score.reject_reason = AssocScore::RejectReason::EuclideanGate;
        return score;
    }
    if (cfg.track_use_mahalanobis_gate) {
        if (!mahalanobis_available) {
            score.reject_reason = AssocScore::RejectReason::InvalidCovariance;
            return score;
        }
        if (effective_chi2_gate > 0.0 &&
            score.mahalanobis_d2 > effective_chi2_gate) {
            score.reject_reason = AssocScore::RejectReason::MahalanobisGate;
            return score;
        }
    }
    if (cfg.track_use_max_speed_gate && safeNonnegative(cfg.track_v_max) > 0.0 &&
        score.instant_speed_mps > safeNonnegative(cfg.track_v_max)) {
        score.reject_reason = AssocScore::RejectReason::MaxSpeedGate;
        return score;
    }
    if (cfg.track_use_heading_gate && score.heading_reference_available &&
        safeNonnegative(cfg.track_heading_gate_deg) > 0.0 &&
        score.heading_diff_deg > safeNonnegative(cfg.track_heading_gate_deg)) {
        score.reject_reason = AssocScore::RejectReason::HeadingGate;
        return score;
    }
    if (cfg.track_use_detection_speed_gate && score.detection_speed_available &&
        safeNonnegative(cfg.track_detection_speed_gate_mps) > 0.0 &&
        score.detection_speed_diff_mps >
            safeNonnegative(cfg.track_detection_speed_gate_mps)) {
        score.reject_reason = AssocScore::RejectReason::DetectionSpeedGate;
        return score;
    }

    const int distance_mode = normalizedDistanceMode(cfg);
    if (distance_mode == static_cast<int>(TrackDistanceMode::MahalanobisSquared)) {
        if (!mahalanobis_available) {
            score.reject_reason = AssocScore::RejectReason::InvalidCovariance;
            return score;
        }
        const double scale = safeNonnegative(cfg.track_mahalanobis_cost_scale) > 0.0
            ? safeNonnegative(cfg.track_mahalanobis_cost_scale)
            : effective_chi2_gate;
        score.distance_cost = scale > kNumericEpsilon
            ? score.mahalanobis_d2 / scale
            : score.mahalanobis_d2;
    } else {
        const double scale = safeNonnegative(cfg.track_euclidean_cost_scale_m) > 0.0
            ? safeNonnegative(cfg.track_euclidean_cost_scale_m)
            : effective_gate_m;
        if (scale > kNumericEpsilon) {
            const double normalized = score.euclidean_dist_m / scale;
            score.distance_cost = normalized * normalized;
        } else {
            static bool warned_unscaled_euclidean = false;
            if (!warned_unscaled_euclidean) {
                std::cerr << "[TRACK][WARN] Euclidean distance cost has no positive scale/gate; "
                          << "raw meters will be compared with dummy_cost." << std::endl;
                warned_unscaled_euclidean = true;
            }
            score.distance_cost = score.euclidean_dist_m;
        }
    }

    if (score.speed_reference_available) {
        double scale = safeNonnegative(cfg.track_speed_cost_scale_mps);
        if (scale <= 0.0) {
            scale = std::max(reference_speed, 1.0);
        }
        score.speed_cost = score.speed_diff_mps / std::max(scale, kNumericEpsilon);
    }
    if (score.heading_reference_available) {
        double scale = safeNonnegative(cfg.track_heading_cost_scale_deg);
        if (scale <= 0.0) {
            scale = 180.0;
        }
        score.heading_cost = score.heading_diff_deg / std::max(scale, kNumericEpsilon);
    }
    if (score.detection_speed_available) {
        const double scale = safeNonnegative(cfg.track_detection_speed_cost_scale_mps) > 0.0
            ? safeNonnegative(cfg.track_detection_speed_cost_scale_mps)
            : safeNonnegative(cfg.track_detection_speed_gate_mps);
        score.detection_speed_cost = scale > kNumericEpsilon
            ? score.detection_speed_diff_mps / scale
            : score.detection_speed_diff_mps;
    }

    const bool no_cost_component_enabled =
        !cfg.track_use_distance_cost &&
        !cfg.track_use_speed_cost &&
        !cfg.track_use_heading_cost &&
        !cfg.track_use_detection_speed_cost;
    if (no_cost_component_enabled) {
        static bool warned_no_cost = false;
        if (!warned_no_cost) {
            std::cerr << "[TRACK][WARN] all association cost components are disabled; "
                      << "fallback to distance cost with weight 1.0." << std::endl;
            warned_no_cost = true;
        }
        score.total_cost = score.distance_cost;
    } else {
        score.total_cost = 0.0;
        if (cfg.track_use_distance_cost) {
            score.total_cost += safeNonnegative(cfg.track_distance_weight) *
                                score.distance_cost;
        }
        if (cfg.track_use_speed_cost && score.speed_reference_available) {
            score.total_cost += safeNonnegative(cfg.track_speed_smooth_weight) *
                                score.speed_cost;
        }
        if (cfg.track_use_heading_cost && score.heading_reference_available) {
            score.total_cost += safeNonnegative(cfg.track_heading_weight) *
                                score.heading_cost;
        }
        if (cfg.track_use_detection_speed_cost && score.detection_speed_available) {
            score.total_cost += safeNonnegative(cfg.track_detection_speed_weight) *
                                score.detection_speed_cost;
        }
    }

    if (!std::isfinite(score.total_cost) || score.total_cost < 0.0) {
        score.total_cost = kInvalidCost;
        score.reject_reason = AssocScore::RejectReason::InvalidNumber;
        return score;
    }
    score.valid = true;
    return score;
}

// A measurement that lies near a Coasted track but can only be reached by
// exceeding the configured physical speed bound is an outlier of that track,
// not evidence of an independent new target.  Without this guard the failed
// association would immediately create a Tentative track and later pollute
// protocol output after a single recovery measurement.
static bool isCoastedSpeedOutlier(const Config& cfg,
                                  const std::vector<ManagedTrack>& tracks,
                                  const GMTIDetection& det,
                                  double frame_utc)
{
    if (!cfg.track_suppress_coasted_speed_outlier ||
        !cfg.track_use_max_speed_gate ||
        safeNonnegative(cfg.track_v_max) <= 0.0) {
        return false;
    }
    for (const auto& tr : tracks) {
        if (tr.state != TrackState::Coasted) continue;
        const double measurement_std = std::max(1.0, cfg.track_measurement_noise_pos);
        const AssocScore score = computeAssocScoreEN(
            cfg, tr, det.e, det.n, det.utcMid, det.speed, frame_utc,
            measurement_std * measurement_std);
        if (score.reject_reason == AssocScore::RejectReason::MaxSpeedGate &&
            std::isfinite(score.euclidean_dist_m) &&
            score.euclidean_dist_m <= safeNonnegative(cfg.track_gate_m)) {
            return true;
        }
    }
    return false;
}

static AssocScore computeAssocScore(const Config& cfg,
                                    const ManagedTrack& tr,
                                    const GMTIDetection& det,
                                    double frame_utc,
                                    double measurement_var)
{
    return computeAssocScoreEN(cfg, tr, det.e, det.n, det.utcMid, det.speed,
                               frame_utc, measurement_var);
}

static std::vector<int> solveAssignment(const std::vector<std::vector<double>>& cost)
{
    const int n = static_cast<int>(cost.size());
    if (n == 0) {
        return std::vector<int>();
    }
    const int m = static_cast<int>(cost.front().size());
    if (m == 0 || n > m) {
        return std::vector<int>(n, -1);
    }

    std::vector<double> u(n + 1, 0.0), v(m + 1, 0.0);
    std::vector<int> p(m + 1, 0), way(m + 1, 0);

    for (int i = 1; i <= n; ++i) {
        p[0] = i;
        int j0 = 0;
        std::vector<double> minv(m + 1, kInvalidCost);
        std::vector<char> used(m + 1, 0);
        do {
            used[j0] = 1;
            const int i0 = p[j0];
            double delta = kInvalidCost;
            int j1 = 0;
            for (int j = 1; j <= m; ++j) {
                if (used[j]) {
                    continue;
                }
                const double cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
                if (cur < minv[j]) {
                    minv[j] = cur;
                    way[j] = j0;
                }
                if (minv[j] < delta) {
                    delta = minv[j];
                    j1 = j;
                }
            }
            if (delta >= kInvalidCost * 0.5) {
                break;
            }
            for (int j = 0; j <= m; ++j) {
                if (used[j]) {
                    u[p[j]] += delta;
                    v[j] -= delta;
                } else {
                    minv[j] -= delta;
                }
            }
            j0 = j1;
        } while (p[j0] != 0);

        do {
            const int j1 = way[j0];
            p[j0] = p[j1];
            j0 = j1;
        } while (j0 != 0);
    }

    std::vector<int> assignment(n, -1);
    for (int j = 1; j <= m; ++j) {
        if (p[j] > 0 && p[j] <= n) {
            assignment[p[j] - 1] = j - 1;
        }
    }
    return assignment;
}

struct GreedyEdge {
    int row = -1;
    int det = -1;
    double cost = kInvalidCost;
};

static std::vector<int> solveGreedyNearestNeighbor(
    const std::vector<std::vector<double>>& cost,
    int det_count,
    double dummy_cost,
    bool allow_equal_dummy_cost,
    bool allow_detection_reuse)
{
    const int row_count = static_cast<int>(cost.size());
    std::vector<int> assignment(row_count, -1);
    if (row_count == 0 || det_count <= 0) {
        return assignment;
    }

    std::vector<GreedyEdge> edges;
    edges.reserve(static_cast<size_t>(row_count) * static_cast<size_t>(det_count));
    for (int row = 0; row < row_count; ++row) {
        const int usable_dets = std::min<int>(det_count, cost[row].size());
        for (int det = 0; det < usable_dets; ++det) {
            if (!costBeatsDummy(cost[row][det], dummy_cost, allow_equal_dummy_cost)) {
                continue;
            }
            GreedyEdge edge;
            edge.row = row;
            edge.det = det;
            edge.cost = cost[row][det];
            edges.push_back(edge);
        }
    }
    std::sort(edges.begin(), edges.end(), [](const GreedyEdge& a, const GreedyEdge& b) {
        if (a.cost != b.cost) return a.cost < b.cost;
        if (a.row != b.row) return a.row < b.row;
        return a.det < b.det;
    });

    std::vector<char> row_used(row_count, 0);
    std::vector<char> det_used(det_count, 0);
    for (const auto& edge : edges) {
        if (!row_used[edge.row] &&
            (allow_detection_reuse || !det_used[edge.det])) {
            assignment[edge.row] = edge.det;
            row_used[edge.row] = 1;
            if (!allow_detection_reuse) {
                det_used[edge.det] = 1;
            }
        }
    }
    return assignment;
}

static std::vector<int> solveConfiguredAssignment(
    const Config& cfg,
    const std::vector<std::vector<double>>& cost,
    int det_count,
    double dummy_cost)
{
    const int assignment_mode = normalizedAssignmentMode(cfg);
    if (cfg.track_allow_detection_reuse ||
        assignment_mode ==
            static_cast<int>(TrackAssignmentMode::GreedyNearestNeighbor)) {
        static bool warned_reuse_forces_greedy = false;
        if (cfg.track_allow_detection_reuse &&
            assignment_mode == static_cast<int>(TrackAssignmentMode::Hungarian) &&
            !warned_reuse_forces_greedy) {
            std::cerr << "[TRACK][WARN] track_allow_detection_reuse=true requires "
                      << "GreedyNearestNeighbor; Hungarian request is ignored."
                      << std::endl;
            warned_reuse_forces_greedy = true;
        }
        return solveGreedyNearestNeighbor(cost, det_count, dummy_cost,
                                          cfg.track_allow_equal_dummy_cost,
                                          cfg.track_allow_detection_reuse);
    }

    std::vector<int> assignment = solveAssignment(cost);
    for (size_t row = 0; row < assignment.size(); ++row) {
        const int col = assignment[row];
        if (col < 0 || col >= det_count ||
            row >= cost.size() || col >= static_cast<int>(cost[row].size()) ||
            !costBeatsDummy(cost[row][col], dummy_cost,
                            cfg.track_allow_equal_dummy_cost)) {
            assignment[row] = -1;
        }
    }
    return assignment;
}

static double effectiveDummyCost(const Config& cfg)
{
    const double value = safeNonnegative(cfg.track_dummy_cost);
    return value > kNumericEpsilon ? value : 1.5;
}

static double effectiveInvalidCost(const Config& cfg, double dummy_cost)
{
    double value = safeNonnegative(cfg.track_invalid_cost);
    if (!std::isfinite(value) || value <= dummy_cost) {
        value = std::max(kInvalidCost, dummy_cost * 1.0e6);
    }
    return value;
}

static void printAssociationConfig(const Config& cfg)
{
    const int distance_mode = normalizedDistanceMode(cfg);
    const int assignment_mode = normalizedAssignmentMode(cfg);
    std::cout << "[TRACK_CONFIG]\n"
              << "distance_mode=" << distanceModeName(distance_mode) << "\n"
              << "assignment_mode=" << assignmentModeName(assignment_mode) << "\n"
              << "output_state_source="
              << outputStateSourceName(normalizedTrackOutputStateSource(cfg))
              << "\n\n"
              << "costs:\n"
              << "distance=" << (cfg.track_use_distance_cost ? "on" : "off")
              << " weight=" << safeNonnegative(cfg.track_distance_weight) << "\n"
              << "speed=" << (cfg.track_use_speed_cost ? "on" : "off")
              << " weight=" << safeNonnegative(cfg.track_speed_smooth_weight) << "\n"
              << "heading=" << (cfg.track_use_heading_cost ? "on" : "off")
              << " weight=" << safeNonnegative(cfg.track_heading_weight) << "\n"
              << "detection_speed=" << (cfg.track_use_detection_speed_cost ? "on" : "off")
              << " weight=" << safeNonnegative(cfg.track_detection_speed_weight) << "\n\n"
              << "gates:\n"
              << "euclidean=" << (cfg.track_use_euclidean_gate ? "on" : "off")
              << " threshold=" << safeNonnegative(cfg.track_gate_m) << "m\n"
              << "mahalanobis=" << (cfg.track_use_mahalanobis_gate ? "on" : "off")
              << " threshold=" << safeNonnegative(cfg.track_chi2_gate) << "\n"
              << "max_speed=" << (cfg.track_use_max_speed_gate ? "on" : "off")
              << " threshold=" << safeNonnegative(cfg.track_v_max) << "m/s\n"
              << "heading=" << (cfg.track_use_heading_gate ? "on" : "off")
              << " threshold=" << safeNonnegative(cfg.track_heading_gate_deg) << "deg\n"
              << "detection_speed=" << (cfg.track_use_detection_speed_gate ? "on" : "off")
              << " threshold=" << safeNonnegative(cfg.track_detection_speed_gate_mps)
              << "m/s\n\n"
              << "dummy_cost=" << effectiveDummyCost(cfg)
              << " invalid_cost=" << effectiveInvalidCost(cfg, effectiveDummyCost(cfg))
              << std::endl;
}

static bool ensureDirectory(const std::string& path)
{
    if (path.empty()) {
        return false;
    }

    std::string current;
    current.reserve(path.size());
    for (size_t i = 0; i < path.size(); ++i) {
        current.push_back(path[i]);
        if (path[i] != '/' && i + 1 != path.size()) {
            continue;
        }
        while (current.size() > 1 && current.back() == '/') {
            current.pop_back();
        }
        if (current.empty() || current == "/") {
            if (path[i] == '/' && current != "/") {
                current.push_back('/');
            }
            continue;
        }
        if (::mkdir(current.c_str(), 0777) != 0 && errno != EEXIST) {
            std::cerr << "[TRACK][WARN] cannot create debug dir: "
                      << current << " error=" << std::strerror(errno) << std::endl;
            return false;
        }
        if (i + 1 != path.size() && path[i] == '/' && current.back() != '/') {
            current.push_back('/');
        }
    }
    return true;
}

static std::string debugDir(const Config& cfg)
{
    if (!cfg.track_debug_dir.empty()) {
        return cfg.track_debug_dir;
    }
    if (cfg.result_add.empty()) {
        return "track_debug";
    }
    return cfg.result_add + "/track_debug";
}

static bool fileNeedsHeader(const std::string& path)
{
    std::ifstream in(path.c_str(), std::ios::binary | std::ios::ate);
    return !in.is_open() || in.tellg() <= 0;
}

static std::ofstream openCsvAppend(const std::string& path, const char* header)
{
    const bool need_header = fileNeedsHeader(path);
    std::ofstream out(path.c_str(), std::ios::out | std::ios::app);
    if (!out.is_open()) {
        std::cerr << "[TRACK][WARN] cannot open debug csv: " << path << std::endl;
        return out;
    }
    if (need_header) {
        out << header << "\n";
    }
    out << std::fixed;
    return out;
}

} // namespace

bool runTrackAssociationSelfTests(std::ostream& out)
{
    int passed = 0;
    int failed = 0;
    const auto check = [&](bool condition, const char* name) {
        if (condition) {
            ++passed;
            out << "[PASS] " << name << "\n";
        } else {
            ++failed;
            out << "[FAIL] " << name << "\n";
        }
    };

    {
        std::string canonical;
        check(gmti::tracking::canonicalizeTrackOutputStateSource(
                  "DETECTION", canonical) && canonical == "measurement",
              "output source detection alias canonicalization");
        check(gmti::tracking::canonicalizeTrackOutputStateSource(
                  "KALMAN_SMOOTHED", canonical) && canonical == "kalman_filtered",
              "output source smoothed alias is labeled filtered");
        check(!gmti::tracking::canonicalizeTrackOutputStateSource(
                  "unknown_source", canonical),
              "invalid output source is rejected");
    }

    Config cfg;
    cfg.track_assignment_mode = 0;
    cfg.track_allow_equal_dummy_cost = false;
    cfg.track_allow_detection_reuse = false;
    {
        const std::vector<std::vector<double>> cost{{0.2, 1.0, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, cost, 2, 1.5);
        check(a.size() == 1 && a[0] == 0, "single track single/best measurement");
    }
    {
        const std::vector<std::vector<double>> cost{
            {2.0, 3.0, 10.0, 10.0},
            {2.1, 100.0, 10.0, 10.0}};
        const std::vector<int> greedy =
            solveGreedyNearestNeighbor(cost, 2, 10.0, false, false);
        check(greedy[0] == 0 && greedy[1] == -1,
              "global greedy edge ordering");
        cfg.track_assignment_mode = 1;
        const std::vector<int> hungarian =
            solveConfiguredAssignment(cfg, cost, 2, 10.0);
        check(hungarian[0] == 1 && hungarian[1] == 0,
              "Hungarian differs from greedy and minimizes total");
        const std::vector<std::vector<double>> three_by_three{
            {18.0, 10.0, 14.0},
            {16.0, 13.0, 16.0},
            {10.0, 18.0, 11.0}};
        const std::vector<int> global_three =
            solveConfiguredAssignment(cfg, three_by_three, 3, 100.0);
        check(global_three.size() == 3 && global_three[0] == 1 &&
                  global_three[1] == 2 && global_three[2] == 0,
              "Hungarian 3x3 global optimum regression");
        cfg.track_assignment_mode = 0;
    }
    {
        const std::vector<std::vector<double>> cost{
            {0.1, 10.0, 10.0},
            {0.2, 10.0, 10.0}};
        const std::vector<int> one_to_one =
            solveGreedyNearestNeighbor(cost, 2, 10.0, false, false);
        const std::vector<int> reusable =
            solveGreedyNearestNeighbor(cost, 2, 10.0, false, true);
        check(one_to_one[0] == 0 && one_to_one[1] == -1,
              "greedy one-to-one detection ownership");
        check(reusable[0] == 0 && reusable[1] == 0,
              "greedy allows repeated detection association when configured");
        cfg.track_assignment_mode = 1;
        cfg.track_allow_detection_reuse = true;
        const std::vector<int> forced_reusable =
            solveConfiguredAssignment(cfg, cost, 2, 10.0);
        check(forced_reusable[0] == 0 && forced_reusable[1] == 0,
              "detection reuse forces greedy instead of Hungarian");
        cfg.track_assignment_mode = 0;
        cfg.track_allow_detection_reuse = false;
    }
    {
        const std::vector<std::vector<double>> cost{
            {1.0e12, 1.0e12, 1.5},
            {1.0e12, 1.0e12, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, cost, 2, 1.5);
        check(a[0] == -1 && a[1] == -1, "no valid candidate");
    }
    {
        const std::vector<std::vector<double>> cost{
            {2.0, 3.0, 1.5},
            {4.0, 5.0, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, cost, 2, 1.5);
        check(a[0] == -1 && a[1] == -1, "real costs above dummy");
    }
    {
        const std::vector<std::vector<double>> cost{
            {0.5, 0.5, 1.5},
            {0.5, 0.5, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, cost, 2, 1.5);
        check(a[0] == 0 && a[1] == 1, "greedy tie is reproducible");
    }
    {
        const std::vector<std::vector<double>> fewer{
            {0.1, 1.5, 1.5, 1.5},
            {0.2, 1.5, 1.5, 1.5},
            {0.3, 1.5, 1.5, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, fewer, 1, 1.5);
        int matched = 0;
        for (int v : a) matched += (v >= 0 ? 1 : 0);
        check(matched == 1, "fewer detections than tracks");
    }
    {
        const std::vector<std::vector<double>> more{
            {0.4, 0.3, 0.2, 1.5},
            {0.1, 0.5, 0.6, 1.5}};
        const std::vector<int> a = solveConfiguredAssignment(cfg, more, 3, 1.5);
        check(a[0] == 2 && a[1] == 0, "more detections than tracks");
    }
    {
        ManagedTrack tr;
        tr.e = 0.0;
        tr.n = 0.0;
        tr.P.fill(0.0);
        tr.hit_count = 1;
        tr.point_history.push_back(TrackPoint{0.0, 0.0, 1.0, 1});
        Config euclidean_cfg;
        euclidean_cfg.track_distance_mode = 0;
        euclidean_cfg.track_use_mahalanobis_gate = false;
        euclidean_cfg.track_use_euclidean_gate = false;
        euclidean_cfg.track_use_max_speed_gate = false;
        const AssocScore euclidean = computeAssocScoreEN(
            euclidean_cfg, tr, 1.0, 0.0, 2.0, 0.0, 2.0, 0.0);
        check(euclidean.valid, "singular covariance allowed in Euclidean mode");
        euclidean_cfg.track_distance_mode = 1;
        const AssocScore mahal = computeAssocScoreEN(
            euclidean_cfg, tr, 1.0, 0.0, 2.0, 0.0, 2.0, 0.0);
        check(!mahal.valid &&
              mahal.reject_reason == AssocScore::RejectReason::InvalidCovariance,
              "singular covariance rejected in Mahalanobis mode");
    }
    {
        ManagedTrack tr;
        tr.e = 0.0;
        tr.n = 0.0;
        initCovarianceOnline(tr);
        Config fallback_cfg;
        fallback_cfg.track_distance_mode = 0;
        fallback_cfg.track_use_mahalanobis_gate = false;
        fallback_cfg.track_use_max_speed_gate = false;
        fallback_cfg.track_use_distance_cost = false;
        fallback_cfg.track_use_speed_cost = false;
        fallback_cfg.track_use_heading_cost = false;
        fallback_cfg.track_use_detection_speed_cost = false;
        const AssocScore score = computeAssocScoreEN(
            fallback_cfg, tr, 30.0, 0.0, 1.0, 0.0, 1.0, 100.0);
        check(score.valid && score.total_cost == score.distance_cost &&
              score.total_cost > 0.0, "all costs disabled fallback");
        fallback_cfg.track_distance_weight = 0.0;
        fallback_cfg.track_use_distance_cost = true;
        const AssocScore zero_weight = computeAssocScoreEN(
            fallback_cfg, tr, 30.0, 0.0, 1.0, 0.0, 1.0, 100.0);
        check(zero_weight.valid && zero_weight.total_cost == 0.0,
              "zero weight removes enabled component");
    }
    {
        ManagedTrack confirmed;
        confirmed.state = TrackState::Confirmed;
        initCovarianceOnline(confirmed);
        ManagedTrack tentative = confirmed;
        tentative.state = TrackState::Tentative;
        Config gate_cfg;
        gate_cfg.track_distance_mode = 0;
        gate_cfg.track_use_mahalanobis_gate = false;
        gate_cfg.track_use_max_speed_gate = false;
        gate_cfg.track_gate_m = 100.0;
        gate_cfg.track_tentative_gate_scale = 1.5;
        const AssocScore confirmed_score = computeAssocScoreEN(
            gate_cfg, confirmed, 120.0, 0.0, 1.0, 0.0, 1.0, 100.0);
        const AssocScore tentative_score = computeAssocScoreEN(
            gate_cfg, tentative, 120.0, 0.0, 1.0, 0.0, 1.0, 100.0);
        check(!confirmed_score.valid && tentative_score.valid,
              "tentative gate scaling");
    }
    {
        // Result-protocol invariant: a packet contains the detection associated
        // with a confirmed track in this result period.  Tentative tracks,
        // coasted predictions, unmatched confirmed tracks and stale matches are
        // internal tracking states and must never be emitted as target points.
        Config output_cfg;
        output_cfg.track_confirm_window = 3;
        output_cfg.track_confirm_hits = 2;

        ManagedTrack current;
        current.state = TrackState::Confirmed;
        current.matched_this_frame = true;
        current.last_result_id = 42;
        current.hit_history.push_back(1);
        current.hit_history.push_back(1);
        check(shouldOutputThisFrame(output_cfg, current, 42),
              "protocol emits confirmed current-period detection");

        ManagedTrack tentative = current;
        tentative.state = TrackState::Tentative;
        check(!shouldOutputThisFrame(output_cfg, tentative, 42),
              "protocol rejects tentative track");

        ManagedTrack coasted = current;
        coasted.state = TrackState::Coasted;
        coasted.matched_this_frame = false;
        check(!shouldOutputThisFrame(output_cfg, coasted, 42),
              "protocol rejects coasted prediction");

        ManagedTrack unmatched = current;
        unmatched.matched_this_frame = false;
        check(!shouldOutputThisFrame(output_cfg, unmatched, 42),
              "protocol rejects unmatched confirmed track");

        check(!shouldOutputThisFrame(output_cfg, current, 43),
              "protocol rejects stale confirmed detection");

        GMTIDetection current_detection{};
        current_detection.id = 999;
        current_detection.lon = 118.123456;
        current_detection.lat = 32.654321;
        current_detection.speed = 0.0;
        current_detection.direction = -17.5;
        current_detection.range = 23567.25;
        current_detection.utcMid = 345678.125;
        current_detection.e = 1234.5;
        current_detection.n = -678.25;
        current.id = 17;
        current.speed = 23.75;
        current.matched_det_index = 0;
        GMTIDetection protocol_detection{};
        check(buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection) &&
                  protocol_detection.id == current.id &&
                  protocol_detection.e == current_detection.e &&
                  protocol_detection.n == current_detection.n &&
                  protocol_detection.lon == current_detection.lon &&
                  protocol_detection.lat == current_detection.lat &&
                  protocol_detection.direction == current_detection.direction &&
                  protocol_detection.range == current_detection.range &&
                  protocol_detection.utcMid == current_detection.utcMid &&
                  protocol_detection.speed == current.speed,
              "protocol payload uses matched current detection");

        current.prediction_snapshot.valid = true;
        current.prediction_snapshot.e = 1200.0;
        current.prediction_snapshot.n = -700.0;
        current.prediction_snapshot.ve = 3.0;
        current.prediction_snapshot.vn = 4.0;
        current.prediction_snapshot.utc = 345678.0;
        current.prediction_snapshot.range = 23400.0;
        current.prediction_snapshot.direction = -18.0;
        output_cfg.track_output_state_source = "prediction";
        check(buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection) &&
                  protocol_detection.id == current.id &&
                  protocol_detection.e == current.prediction_snapshot.e &&
                  protocol_detection.n == current.prediction_snapshot.n &&
                  protocol_detection.speed == 5.0 &&
                  protocol_detection.range == current.prediction_snapshot.range &&
                  protocol_detection.direction == current.prediction_snapshot.direction &&
                  protocol_detection.utcMid == current.prediction_snapshot.utc &&
                  protocol_detection.e != current_detection.e &&
                  protocol_detection.range != current_detection.range,
              "prediction payload uses captured prior position and velocity");

        current.prediction_snapshot.range =
            std::numeric_limits<double>::quiet_NaN();
        check(!buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection),
              "prediction payload rejects non-finite ancillary state");
        current.prediction_snapshot.range = 23400.0;

        current.filtered_snapshot.valid = true;
        current.filtered_snapshot.e = 1225.0;
        current.filtered_snapshot.n = -690.0;
        current.filtered_snapshot.ve = 5.0;
        current.filtered_snapshot.vn = 12.0;
        current.filtered_snapshot.utc = current_detection.utcMid;
        current.filtered_snapshot.range = current_detection.range;
        current.filtered_snapshot.direction = current_detection.direction;
        output_cfg.track_output_state_source = "kalman_smoothed";
        check(buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection) &&
                  protocol_detection.id == current.id &&
                  protocol_detection.e == current.filtered_snapshot.e &&
                  protocol_detection.n == current.filtered_snapshot.n &&
                  protocol_detection.speed == 13.0 &&
                  protocol_detection.range == current_detection.range &&
                  protocol_detection.direction == current_detection.direction &&
                  protocol_detection.utcMid == current_detection.utcMid,
              "kalman_smoothed uses filtered position and velocity");

        output_cfg.track_output_state_source = "prediction";
        current.prediction_snapshot.valid = false;
        check(!buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection),
              "prediction mode fails closed when no prior exists");

        current.matched_det_index = 1;
        check(!buildProtocolDetectionForFrame(
                  output_cfg, current, 42, {current_detection}, protocol_detection),
              "protocol rejects invalid matched detection index");
    }
    {
        ManagedTrack history_track;
        history_track.point_history.push_back(TrackPoint{0.0, 0.0, 10.0, 1});
        history_track.point_history.push_back(TrackPoint{20.0, 0.0, 11.0, 2});
        GMTIDetection current{};
        current.e = 30.0;
        current.n = 0.0;
        current.utcMid = 13.0;
        Config history_cfg;
        updateMotionHistoryFromEN(history_track, current, 2.0, history_cfg);
        check(std::abs(history_track.speed - 10.0) < 1.0e-12 &&
                  std::abs(history_track.ve - 10.0) < 1.0e-12 &&
                  std::abs(history_track.vn) < 1.0e-12,
              "final speed uses multi-period displacement over actual elapsed time");
    }
    {
        // End-to-end snapshot lifecycle: the second-period output must expose
        // the state actually captured before/after kalmanUpdate, not synthetic
        // fields assigned only inside the payload helper test above.
        GMTIDetection first{};
        first.id = 1;
        first.lon = 118.0;
        first.lat = 32.0;
        first.direction = -10.0;
        first.range = 1000.0;
        first.utcMid = 1.0;
        first.e = 0.0;
        first.n = 0.0;
        GMTIDetection second = first;
        second.id = 2;
        second.lon = 118.0001;
        second.lat = 32.0001;
        second.direction = -9.0;
        second.range = 1010.0;
        second.utcMid = 2.0;
        second.e = 10.0;
        const std::string audit_dir =
            "/tmp/gmti_track_output_source_selftest_" +
            std::to_string(static_cast<long long>(getpid()));

        const auto run_mode = [&](const char* mode) {
            Config flow_cfg;
            flow_cfg.result_add = audit_dir;
            flow_cfg.runtime_diagnostics_enabled = true;
            flow_cfg.track_debug_level = 1;
            flow_cfg.track_debug_dump = true;
            flow_cfg.track_debug_dump_level = 1;
            flow_cfg.track_confirm_window = 2;
            flow_cfg.track_confirm_hits = 2;
            flow_cfg.track_output_state_source = mode;
            TrackManager manager;
            const std::vector<GMTIDetection> first_out =
                manager.updateRawDetections(flow_cfg, {first}, 1, first.utcMid);
            check(first_out.empty(), "new tentative track is not output");
            return manager.updateRawDetections(flow_cfg, {second}, 2, second.utcMid);
        };

        const std::vector<GMTIDetection> measurement_out = run_mode("measurement");
        check(measurement_out.size() == 1 &&
                  measurement_out[0].e == second.e &&
                  measurement_out[0].range == second.range &&
                  measurement_out[0].utcMid == second.utcMid,
              "end-to-end measurement output uses second-period detection");

        const std::vector<GMTIDetection> prediction_out = run_mode("prediction");
        check(prediction_out.size() == 1 &&
                  prediction_out[0].e == first.e &&
                  prediction_out[0].range == first.range &&
                  prediction_out[0].utcMid == second.utcMid,
              "end-to-end prediction output uses prior snapshot");

        const std::vector<GMTIDetection> filtered_out = run_mode("kalman_filtered");
        check(filtered_out.size() == 1 &&
                  filtered_out[0].e > first.e &&
                  filtered_out[0].e < second.e &&
                  filtered_out[0].range == second.range &&
                  filtered_out[0].utcMid == second.utcMid,
              "end-to-end filtered output uses posterior snapshot");

        std::ifstream audit(audit_dir + "/track_output_payloads.csv");
        std::string audit_header;
        std::getline(audit, audit_header);
        check(audit.good() &&
                  audit_header.find("configured_source,resolved_source") !=
                      std::string::npos &&
                  audit_header.find("prediction_valid") != std::string::npos &&
                  audit_header.find("output_utc") != std::string::npos,
              "output provenance audit CSV schema is present");
    }
    {
        ManagedTrack tr;
        tr.e = 10.0;
        tr.n = 0.0;
        tr.utc = 2.0;
        tr.hit_count = 3;
        initCovarianceOnline(tr);
        tr.point_history.push_back(TrackPoint{0.0, 0.0, 1.0, 1});
        tr.point_history.push_back(TrackPoint{10.0, 0.0, 2.0, 2});
        tr.speed_history.push_back(10.0);
        tr.speed_history.push_back(10.0);
        tr.heading_history.push_back(0.0);
        tr.heading_history.push_back(0.0);

        for (int distance_mode = 0; distance_mode <= 1; ++distance_mode) {
            for (int assignment_mode = 0; assignment_mode <= 1; ++assignment_mode) {
                Config mode_cfg;
                mode_cfg.track_distance_mode = distance_mode;
                mode_cfg.track_assignment_mode = assignment_mode;
                mode_cfg.track_use_max_speed_gate = false;
                const AssocScore score = computeAssocScoreEN(
                    mode_cfg, tr, 20.0, 0.0, 3.0, 10.0, 3.0, 100.0);
                std::vector<std::vector<double>> cost(
                    1, std::vector<double>(2, effectiveDummyCost(mode_cfg)));
                cost[0][0] = score.total_cost;
                const std::vector<int> a = solveConfiguredAssignment(
                    mode_cfg, cost, 1, effectiveDummyCost(mode_cfg));
                check(score.valid && a.size() == 1 && a[0] == 0,
                      distance_mode == 0
                          ? (assignment_mode == 0
                              ? "Euclidean + Greedy"
                              : "Euclidean + Hungarian")
                          : (assignment_mode == 0
                              ? "Mahalanobis + Greedy"
                              : "Mahalanobis + Hungarian"));
            }
        }

        for (int cost_case = 0; cost_case < 4; ++cost_case) {
            Config cost_cfg;
            cost_cfg.track_use_max_speed_gate = false;
            cost_cfg.track_use_speed_cost = (cost_case == 1 || cost_case == 3);
            cost_cfg.track_use_heading_cost = (cost_case == 2 || cost_case == 3);
            cost_cfg.track_speed_smooth_weight = 0.3;
            cost_cfg.track_heading_weight = 0.5;
            const AssocScore score = computeAssocScoreEN(
                cost_cfg, tr, 20.0, 0.0, 3.0, 10.0, 3.0, 100.0);
            check(score.valid && std::isfinite(score.total_cost),
                  cost_case == 0 ? "distance-only cost"
                  : cost_case == 1 ? "distance + speed cost"
                  : cost_case == 2 ? "distance + heading cost"
                                   : "distance + speed + heading cost");
        }

        Config cross_gate_cfg;
        cross_gate_cfg.track_distance_mode = 1;
        cross_gate_cfg.track_use_mahalanobis_gate = false;
        cross_gate_cfg.track_use_max_speed_gate = false;
        check(computeAssocScoreEN(cross_gate_cfg, tr, 20.0, 0.0, 3.0,
                                  10.0, 3.0, 100.0).valid,
              "Mahalanobis cost with Mahalanobis gate disabled");
        cross_gate_cfg.track_distance_mode = 0;
        cross_gate_cfg.track_use_mahalanobis_gate = true;
        check(computeAssocScoreEN(cross_gate_cfg, tr, 20.0, 0.0, 3.0,
                                  10.0, 3.0, 100.0).valid,
              "Euclidean cost with Mahalanobis gate enabled");
    }
    {
        ManagedTrack heading_track;
        heading_track.e = 0.0;
        heading_track.n = 0.0;
        heading_track.utc = 2.0;
        heading_track.hit_count = 3;
        initCovarianceOnline(heading_track);
        heading_track.point_history.push_back(TrackPoint{0.0, 0.0, 1.0, 1});
        heading_track.point_history.push_back(TrackPoint{0.0, 0.0, 2.0, 2});
        heading_track.speed_history.push_back(10.0);
        heading_track.speed_history.push_back(10.0);
        heading_track.heading_history.push_back(0.0);
        heading_track.heading_history.push_back(0.0);
        Config heading_cfg;
        heading_cfg.track_use_max_speed_gate = false;
        heading_cfg.track_heading_min_displacement_m = 5.0;
        const AssocScore below = computeAssocScoreEN(
            heading_cfg, heading_track, 2.0, 0.0, 3.0, 10.0, 3.0, 100.0);
        const AssocScore above = computeAssocScoreEN(
            heading_cfg, heading_track, 10.0, 0.0, 3.0, 10.0, 3.0, 100.0);
        check(below.valid && !below.heading_reference_available &&
                  above.valid && above.heading_reference_available,
              "heading cost minimum displacement guard");
    }

    const auto make_flow_cfg = []() {
        Config flow_cfg;
        flow_cfg.result_add = "/tmp/gmti_track_assignment_selftest";
        flow_cfg.runtime_diagnostics_enabled = false;
        flow_cfg.track_debug_level = 0;
        flow_cfg.track_debug_dump = false;
        flow_cfg.track_debug_dump_level = 0;
        flow_cfg.track_confirm_window = 2;
        flow_cfg.track_confirm_hits = 2;
        flow_cfg.track_max_missed = 2;
        flow_cfg.track_tentative_max_missed = 1;
        flow_cfg.track_distance_mode = 0;
        flow_cfg.track_assignment_mode = 1;
        flow_cfg.track_allow_detection_reuse = false;
        flow_cfg.track_use_distance_cost = true;
        flow_cfg.track_use_speed_cost = false;
        flow_cfg.track_use_heading_cost = false;
        flow_cfg.track_use_detection_speed_cost = false;
        flow_cfg.track_use_euclidean_gate = true;
        flow_cfg.track_use_mahalanobis_gate = false;
        flow_cfg.track_use_max_speed_gate = true;
        flow_cfg.track_gate_m = 50.0;
        flow_cfg.track_v_max = 50.0;
        flow_cfg.track_tentative_gate_scale = 1.0;
        flow_cfg.track_tentative_chi2_scale = 1.0;
        flow_cfg.track_dummy_cost = 1.5;
        flow_cfg.track_measurement_noise_pos = 10.0;
        flow_cfg.track_process_noise_pos = 1.0;
        flow_cfg.track_process_noise_vel = 1.0;
        flow_cfg.track_output_state_source = "measurement";
        return flow_cfg;
    };
    const auto make_det = [](double e, double n, double utc, int det_id) {
        GMTIDetection det{};
        det.id = static_cast<uint16_t>(det_id);
        det.e = e;
        det.n = n;
        det.utcMid = utc;
        det.range = std::sqrt(e * e + n * n);
        det.direction = gmti::trig_lut::atan2(n, e) * kRadToDeg;
        return det;
    };
    const auto active_track_count = [](const TrackManager& manager) {
        return static_cast<int>(std::count_if(
            manager.tracks_.begin(), manager.tracks_.end(),
            [](const ManagedTrack& tr) { return tr.state != TrackState::Deleted; }));
    };

    {
        // T1: one constant-velocity target for nine periods keeps one ID.
        Config flow_cfg = make_flow_cfg();
        TrackManager manager;
        bool stable = true;
        for (int period = 1; period <= 9; ++period) {
            const auto outputs = manager.updateRawDetections(
                flow_cfg, {make_det(10.0 * (period - 1), 0.0,
                                    static_cast<double>(period), period)},
                period, static_cast<double>(period));
            stable = stable && active_track_count(manager) == 1 &&
                     manager.tracks_.front().id == 1 &&
                     manager.tracks_.front().matched_this_frame;
            if (period >= 2) {
                stable = stable && outputs.size() == 1 && outputs.front().id == 1;
            }
        }
        check(stable, "T1 constant-velocity target keeps one track id for 9 periods");
    }
    {
        // T2: a true near point and an out-of-distance-gate interferer coexist.
        Config flow_cfg = make_flow_cfg();
        TrackManager manager;
        manager.updateRawDetections(flow_cfg, {make_det(0.0, 0.0, 1.0, 1)}, 1, 1.0);
        manager.updateRawDetections(flow_cfg, {make_det(10.0, 0.0, 2.0, 2)}, 2, 2.0);
        manager.updateRawDetections(
            flow_cfg,
            {make_det(500.0, 0.0, 3.0, 30), make_det(20.0, 0.0, 3.0, 31)},
            3, 3.0);
        const ManagedTrack* original = manager.findTrackById(1);
        check(original && original->matched_this_frame &&
                  original->matched_det_index == 1 &&
                  std::fabs(original->e - 20.0) < 1.0e-9 &&
                  active_track_count(manager) == 2,
              "T2 out-of-distance-gate point cannot replace the true point");
    }
    {
        // T3: with only an out-of-gate point, the original track must coast.
        Config flow_cfg = make_flow_cfg();
        TrackManager manager;
        manager.updateRawDetections(flow_cfg, {make_det(0.0, 0.0, 1.0, 1)}, 1, 1.0);
        manager.updateRawDetections(flow_cfg, {make_det(10.0, 0.0, 2.0, 2)}, 2, 2.0);
        manager.updateRawDetections(flow_cfg, {make_det(500.0, 0.0, 3.0, 3)}, 3, 3.0);
        const ManagedTrack* original = manager.findTrackById(1);
        check(original && !original->matched_this_frame &&
                  original->state == TrackState::Coasted &&
                  original->miss_count == 1 && active_track_count(manager) == 2,
              "T3 only out-of-gate point leaves original track missed");
    }
    {
        // T4: a point inside the spatial gate but above the speed gate is rejected.
        Config flow_cfg = make_flow_cfg();
        flow_cfg.track_gate_m = 100.0;
        TrackManager manager;
        manager.updateRawDetections(flow_cfg, {make_det(0.0, 0.0, 1.0, 1)}, 1, 1.0);
        manager.updateRawDetections(flow_cfg, {make_det(80.0, 0.0, 2.0, 2)}, 2, 2.0);
        const ManagedTrack* original = manager.findTrackById(1);
        check(original && !original->matched_this_frame &&
                  original->miss_count == 1 && active_track_count(manager) == 2,
              "T4 above-speed-gate jump cannot update original track");
    }
    {
        // T5: force a chi-square rejection while Euclidean/speed gates pass.
        Config flow_cfg = make_flow_cfg();
        flow_cfg.track_gate_m = 1000.0;
        flow_cfg.track_use_max_speed_gate = false;
        flow_cfg.track_use_mahalanobis_gate = true;
        flow_cfg.track_chi2_gate = 0.01;
        TrackManager manager;
        manager.updateRawDetections(flow_cfg, {make_det(0.0, 0.0, 1.0, 1)}, 1, 1.0);
        manager.updateRawDetections(flow_cfg, {make_det(100.0, 0.0, 2.0, 2)}, 2, 2.0);
        const ManagedTrack* original = manager.findTrackById(1);
        check(original && !original->matched_this_frame &&
                  original->miss_count == 1 && active_track_count(manager) == 2,
              "T5 chi-square-gate failure cannot update original track");
    }
    {
        // T6: two coasted periods must advance the state epoch once per period,
        // then recover the original ID at the physically predicted position.
        Config flow_cfg = make_flow_cfg();
        flow_cfg.track_gate_m = 20.0;
        TrackManager manager;
        manager.updateRawDetections(flow_cfg, {make_det(0.0, 0.0, 1.0, 1)}, 1, 1.0);
        manager.updateRawDetections(flow_cfg, {make_det(10.0, 0.0, 2.0, 2)}, 2, 2.0);
        flow_cfg.track_gate_m = 5.0;
        manager.updateRawDetections(flow_cfg, {}, 3, 3.0);
        manager.updateRawDetections(flow_cfg, {}, 4, 4.0);
        const ManagedTrack* before_recovery = manager.findTrackById(1);
        const bool coast_prediction_ok = before_recovery &&
            std::fabs(before_recovery->e - 30.0) < 1.0e-9 &&
            std::fabs(before_recovery->state_utc - 4.0) < 1.0e-9;
        manager.updateRawDetections(
            flow_cfg, {make_det(40.0, 0.0, 5.0, 5)}, 5, 5.0);
        const ManagedTrack* recovered = manager.findTrackById(1);
        check(coast_prediction_ok && recovered && recovered->matched_this_frame &&
                  recovered->state == TrackState::Confirmed &&
                  recovered->miss_count == 0 && recovered->id == 1 &&
                  active_track_count(manager) == 1,
              "T6 coasted state advances once and recovers original id");
    }
    {
        // T7: two tracks and reversed detection ordering remain one-to-one.
        Config flow_cfg = make_flow_cfg();
        TrackManager manager;
        manager.updateRawDetections(
            flow_cfg, {make_det(0.0, 0.0, 1.0, 1), make_det(0.0, 100.0, 1.0, 2)},
            1, 1.0);
        manager.updateRawDetections(
            flow_cfg, {make_det(10.0, 0.0, 2.0, 3), make_det(10.0, 100.0, 2.0, 4)},
            2, 2.0);
        manager.updateRawDetections(
            flow_cfg, {make_det(20.0, 100.0, 3.0, 5), make_det(20.0, 0.0, 3.0, 6)},
            3, 3.0);
        const ManagedTrack* first = manager.findTrackById(1);
        const ManagedTrack* second = manager.findTrackById(2);
        check(first && second && first->matched_this_frame && second->matched_this_frame &&
                  first->matched_det_index != second->matched_det_index &&
                  std::fabs(first->n) < 1.0e-9 &&
                  std::fabs(second->n - 100.0) < 1.0e-9 &&
                  active_track_count(manager) == 2,
              "T7 two targets remain one-to-one with reversed detection order");
    }
    {
        // T8: all real edges invalid; finite invalid costs must never beat dummies.
        Config flow_cfg = make_flow_cfg();
        flow_cfg.track_gate_m = 20.0;
        TrackManager manager;
        manager.updateRawDetections(
            flow_cfg, {make_det(0.0, 0.0, 1.0, 1), make_det(0.0, 100.0, 1.0, 2)},
            1, 1.0);
        manager.updateRawDetections(
            flow_cfg, {make_det(500.0, 0.0, 2.0, 3), make_det(500.0, 100.0, 2.0, 4)},
            2, 2.0);
        const ManagedTrack* first = manager.findTrackById(1);
        const ManagedTrack* second = manager.findTrackById(2);
        bool finite_states = true;
        for (const auto& tr : manager.tracks_) {
            finite_states = finite_states && std::isfinite(tr.e) &&
                            std::isfinite(tr.n) && std::isfinite(tr.ve) &&
                            std::isfinite(tr.vn);
        }
        check(first && second && !first->matched_this_frame &&
                  !second->matched_this_frame &&
                  first->matched_det_index < 0 && second->matched_det_index < 0 &&
                  active_track_count(manager) == 4 && finite_states,
              "T8 invalid finite-cost edges cannot update any original track");
    }

    out << "[TRACK_SELFTEST] passed=" << passed << " failed=" << failed << "\n";
    return failed == 0;
}

std::vector<GMTIDetection> TrackManager::update(
    const Config& cfg,
    const std::vector<GMTIDetection>& current_targets)
{
    if (!association_config_logged_ && cfg.runtime_diagnostics_enabled && cfg.track_debug_level > 0) {
        printAssociationConfig(cfg);
        association_config_logged_ = true;
    }
    const int result_id = currentResultId(cfg);
    if (result_id > 0 && last_update_result_id_ > 0 && result_id < last_update_result_id_) {
        std::cout << "[TRACK][WARN] TrackManager result id moved backward: current="
                  << result_id << ", previous=" << last_update_result_id_
                  << ". Reset persistent tracks." << std::endl;
        reset();
    } else if (result_id > 0 && result_id == last_update_result_id_) {
        std::cout << "[TRACK][WARN] TrackManager received duplicate result id: "
                  << result_id << ". Persistent ids will be updated in place." << std::endl;
    }

    std::vector<DetectionPoint> detections;
    detections.reserve(current_targets.size());
    for (const auto& det : current_targets) {
        DetectionPoint pt;
        pt.det = det;
        Gaussp3(det.lat, det.lon, cfg.L0, pt.e, pt.n);
        pt.det.e = pt.e;
        pt.det.n = pt.n;
        detections.push_back(pt);
    }

    const double gate_m = std::max(0.0, cfg.track_gate_m);
    const int max_miss = maxMiss(cfg);
    const double measurement_std = std::max(kMinMeasurementStdM, gate_m * 0.25);
    const double measurement_var = measurement_std * measurement_std;
    const double update_utc = currentUtc(detections);

    std::vector<int> active_tracks;
    active_tracks.reserve(tracks_.size());
    for (size_t ti = 0; ti < tracks_.size(); ++ti) {
        ManagedTrack& tr = tracks_[ti];
        if (tr.state == TrackState::Deleted || tr.miss_count > max_miss) {
            continue;
        }
        if (result_id > 0 && tr.last_result_id > 0 &&
            result_id - tr.last_result_id > max_miss + 1) {
            continue;
        }
        if (tr.hit_count <= 0) {
            initCovarianceLegacy(tr, gate_m);
        }
        predictTrackLegacy(tr, frameDtLegacy(tr, update_utc, result_id));
        active_tracks.push_back(static_cast<int>(ti));
    }

    std::vector<char> track_used(tracks_.size(), 0);
    std::vector<char> det_used(detections.size(), 0);
    const int row_count = static_cast<int>(active_tracks.size());
    const int det_count = static_cast<int>(detections.size());
    if (row_count > 0) {
        const int col_count = det_count + row_count;
        const double dummy_cost = effectiveDummyCost(cfg);
        const double invalid_cost = effectiveInvalidCost(cfg, dummy_cost);
        std::vector<std::vector<double>> cost(
            row_count, std::vector<double>(col_count, dummy_cost));
        std::vector<std::vector<AssocScore>> scores(
            row_count, std::vector<AssocScore>(det_count));

        for (int row = 0; row < row_count; ++row) {
            const ManagedTrack& tr = tracks_[active_tracks[row]];
            for (int di = 0; di < det_count; ++di) {
                scores[row][di] = computeAssocScore(
                    cfg, tr, detections[di].det, update_utc, measurement_var);
                cost[row][di] = scores[row][di].valid
                    ? scores[row][di].total_cost : invalid_cost;
            }
        }

        const std::vector<int> assignment =
            solveConfiguredAssignment(cfg, cost, det_count, dummy_cost);
        if (cfg.track_debug_dump_level >= 2) {
            const std::string dir = debugDir(cfg);
            if (ensureDirectory(dir)) {
                std::ofstream candidates = openCsvAppend(
                    dir + "/association_candidates.csv",
                    "result_id,track_id,track_state,det_index,valid,reject_reason,distance_mode,assignment_mode,euclidean_dist_m,mahalanobis_d2,instant_speed_mps,speed_diff_mps,heading_diff_deg,detection_speed_diff_mps,distance_cost,speed_cost,heading_cost,detection_speed_cost,total_cost,dummy_cost,assigned");
                if (candidates.is_open()) {
                    for (int row = 0; row < row_count; ++row) {
                        const ManagedTrack& tr = tracks_[active_tracks[row]];
                        for (int di = 0; di < det_count; ++di) {
                            const AssocScore& score = scores[row][di];
                            candidates << std::setprecision(12)
                                       << result_id << "," << tr.id << ","
                                       << stateName(tr.state) << "," << di << ","
                                       << (score.valid ? 1 : 0) << ","
                                       << rejectReasonName(score.reject_reason) << ","
                                       << distanceModeName(normalizedDistanceMode(cfg)) << ","
                                       << assignmentModeName(normalizedAssignmentMode(cfg)) << ","
                                       << score.euclidean_dist_m << ","
                                       << score.mahalanobis_d2 << ","
                                       << score.instant_speed_mps << ","
                                       << score.speed_diff_mps << ","
                                       << score.heading_diff_deg << ","
                                       << score.detection_speed_diff_mps << ","
                                       << score.distance_cost << ","
                                       << score.speed_cost << ","
                                       << score.heading_cost << ","
                                       << score.detection_speed_cost << ","
                                       << score.total_cost << ","
                                       << dummy_cost << ","
                                       << ((row < static_cast<int>(assignment.size()) &&
                                            assignment[row] == di) ? 1 : 0)
                                       << "\n";
                        }
                    }
                }
            }
        }
        for (int row = 0; row < row_count; ++row) {
            const int ti = active_tracks[row];
            const int col = (row < static_cast<int>(assignment.size())) ? assignment[row] : -1;
            if (col < 0 || col >= det_count ||
                (!cfg.track_allow_detection_reuse && det_used[col]) ||
                !scores[row][col].valid ||
                !costBeatsDummy(scores[row][col].total_cost, dummy_cost,
                                cfg.track_allow_equal_dummy_cost)) {
                continue;
            }

            ManagedTrack& tr = tracks_[ti];
            const DetectionPoint& pt = detections[col];
            const double dt = frameDtLegacy(tr, pt.det.utcMid, result_id);
            const double prev_e = tr.e;
            const double prev_n = tr.n;
            kalmanUpdate(tr, pt.e, pt.n, measurement_var, nullptr);
            const double dist = distanceEN(pt.e, pt.n, prev_e, prev_n);
            const double speed = dist / std::max(1e-3, dt);

            detections[col].det.id = tr.id;
            tr.e = pt.e;
            tr.n = pt.n;
            tr.utc = pt.det.utcMid;
            tr.state_utc = tr.utc;
            tr.speed = speed;
            tr.direction = pt.det.direction;
            tr.range = pt.det.range;
            tr.last_result_id = result_id;
            tr.hit_count += 1;
            tr.miss_count = 0;
            tr.state = TrackState::Confirmed;
            tr.recent_result_ids.push_back(result_id);
            while (tr.recent_result_ids.size() > static_cast<size_t>(cfg.track_idx_window)) {
                tr.recent_result_ids.pop_front();
            }

            track_used[ti] = 1;
            det_used[col] = 1;
        }
    }

    for (size_t ti = 0; ti < tracks_.size(); ++ti) {
        if (tracks_[ti].state == TrackState::Deleted) {
            continue;
        }
        if (ti >= track_used.size() || !track_used[ti]) {
            tracks_[ti].miss_count += 1;
            tracks_[ti].state = tracks_[ti].miss_count > max_miss
                ? TrackState::Deleted
                : TrackState::Coasted;
        }
    }

    for (size_t di = 0; di < detections.size(); ++di) {
        if (det_used[di]) {
            continue;
        }
        ManagedTrack tr;
        tr.id = allocateId();
        tr.state = TrackState::Confirmed;
        tr.e = detections[di].e;
        tr.n = detections[di].n;
        tr.ve = 0.0;
        tr.vn = 0.0;
        initCovarianceLegacy(tr, gate_m);
        tr.utc = detections[di].det.utcMid;
        tr.state_utc = tr.utc;
        // One detection has no inter-period displacement, so speed is not yet
        // observable.  Do not seed a final track speed from detector Doppler.
        tr.speed = 0.0;
        tr.direction = detections[di].det.direction;
        tr.range = detections[di].det.range;
        tr.last_result_id = result_id;
        tr.hit_count = 1;
        tr.miss_count = 0;
        tr.recent_result_ids.push_back(result_id);
        detections[di].det.id = tr.id;
        tracks_.push_back(tr);
    }

    pruneDeleted();
    if (result_id > 0) {
        last_update_result_id_ = result_id;
    }

    std::vector<GMTIDetection> out;
    out.reserve(detections.size());
    for (const auto& pt : detections) {
        out.push_back(pt.det);
    }
    std::sort(out.begin(), out.end(), [](const GMTIDetection& a, const GMTIDetection& b) {
        return a.id < b.id;
    });
    return out;
}

std::vector<GMTIDetection> TrackManager::updateRawDetections(
    const Config& cfg,
    const std::vector<GMTIDetection>& current_detections,
    int result_id,
    double frame_utc)
{
    ensureP3CsvHeaders(cfg);
    if (!association_config_logged_ && cfg.runtime_diagnostics_enabled && cfg.track_debug_level > 0) {
        printAssociationConfig(cfg);
        association_config_logged_ = true;
    }
    if (result_id > 0 && last_update_result_id_ > 0 && result_id < last_update_result_id_) {
        std::cout << "[TRACK][WARN] TrackManager result id moved backward: current="
                  << result_id << ", previous=" << last_update_result_id_
                  << ". Reset persistent tracks." << std::endl;
        reset();
    }

    for (auto& tr : tracks_) {
        tr.matched_this_frame = false;
        tr.matched_det_index = -1;
        tr.is_output_this_frame = false;
        tr.prediction_snapshot.valid = false;
        tr.filtered_snapshot.valid = false;
    }

    std::vector<GMTIDetection> dets = current_detections;
    for (auto& tr : tracks_) {
        if (tr.state == TrackState::Deleted) {
            continue;
        }
        const double predict_dt = computeDtForPredict(cfg, tr, frame_utc);
        const double predict_utc = snapshotUtc(tr, frame_utc, predict_dt);
        if (predict_dt > 0.0) {
            kalmanPredict(tr, cfg, predict_dt);
        }
        tr.state_utc = predict_utc;
        // range/direction are not members of the [E,N,vE,vN] Kalman state.
        // The prediction payload therefore uses a documented zero-order hold
        // of their previous matched values, never the current detection.
        captureKinematicSnapshot(tr.prediction_snapshot, tr, predict_utc,
                                 tr.range, tr.direction);
    }

    std::vector<int> active_tracks;
    active_tracks.reserve(tracks_.size());
    for (size_t ti = 0; ti < tracks_.size(); ++ti) {
        if (tracks_[ti].state != TrackState::Deleted) {
            active_tracks.push_back(static_cast<int>(ti));
        }
    }

    std::vector<char> det_used(dets.size(), 0);
    std::vector<int> det_to_track_id(dets.size(), -1);
    std::vector<AssocScore> accepted_scores(tracks_.size());
    int num_matched_tracks = 0;
    int num_new_tracks = 0;
    const int row_count = static_cast<int>(active_tracks.size());
    const int det_count = static_cast<int>(dets.size());
    const double dummy_cost = effectiveDummyCost(cfg);
    const double invalid_cost = effectiveInvalidCost(cfg, dummy_cost);
    const double measurement_std = std::max(1.0, cfg.track_measurement_noise_pos);
    const double measurement_var = measurement_std * measurement_std;

    if (row_count > 0 && det_count > 0) {
        const int col_count = det_count + row_count;
        std::vector<std::vector<double>> cost(row_count, std::vector<double>(col_count, dummy_cost));
        std::vector<std::vector<AssocScore>> scores(row_count, std::vector<AssocScore>(det_count));

        for (int row = 0; row < row_count; ++row) {
            const ManagedTrack& tr = tracks_[active_tracks[row]];
            for (int di = 0; di < det_count; ++di) {
                scores[row][di] = computeAssocScore(cfg, tr, dets[di], frame_utc, measurement_var);
                if (scores[row][di].valid) {
                    cost[row][di] = scores[row][di].total_cost;
                } else {
                    cost[row][di] = invalid_cost;
                    if (cfg.track_debug_level >= 3) {
                        std::cout << "[TRACK_ONLINE] cost invalid track=" << tr.id
                                  << " det=" << di
                                  << " reject_reason="
                                  << rejectReasonName(scores[row][di].reject_reason)
                                  << " d=" << scores[row][di].euclidean_dist_m
                                  << " d2=" << scores[row][di].mahalanobis_d2
                                  << " v=" << scores[row][di].instant_speed_mps
                                  << std::endl;
                    }
                }
            }
        }

        const std::vector<int> assignment =
            solveConfiguredAssignment(cfg, cost, det_count, dummy_cost);
        appendTrackAssociationLog(cfg, result_id, active_tracks, tracks_, dets,
                                  scores, assignment, dummy_cost,
                                  cfg.track_allow_equal_dummy_cost);
        std::ofstream accepted_audit;
        if (detailedTrackDiagnosticsEnabled(cfg)) {
            accepted_audit = openCsvAppend(
                trackEvalDir(cfg) + "/track_association_accepts.csv",
                "result_id,track_id,track_state_before,det_index,last_measurement_utc,frame_utc,detection_utc,predicted_e,predicted_n,detection_e,detection_n,euclidean_dist_m,euclidean_gate_m,mahalanobis_d2,mahalanobis_gate_d2,instant_speed_mps,max_speed_gate_mps,total_cost,dummy_cost,gate_passed,cost_beats_dummy,post_assignment_accepted");
        }
        if (cfg.track_debug_dump_level >= 2) {
            const std::string dir = debugDir(cfg);
            if (ensureDirectory(dir)) {
                std::ofstream candidates = openCsvAppend(
                    dir + "/association_candidates.csv",
                    "result_id,track_id,track_state,det_index,valid,reject_reason,distance_mode,assignment_mode,euclidean_dist_m,mahalanobis_d2,instant_speed_mps,speed_diff_mps,heading_diff_deg,detection_speed_diff_mps,distance_cost,speed_cost,heading_cost,detection_speed_cost,total_cost,dummy_cost,assigned");
                if (candidates.is_open()) {
                    for (int row = 0; row < row_count; ++row) {
                        const ManagedTrack& tr = tracks_[active_tracks[row]];
                        for (int di = 0; di < det_count; ++di) {
                            const AssocScore& score = scores[row][di];
                            candidates << std::setprecision(12)
                                       << result_id << "," << tr.id << ","
                                       << stateName(tr.state) << "," << di << ","
                                       << (score.valid ? 1 : 0) << ","
                                       << rejectReasonName(score.reject_reason) << ","
                                       << distanceModeName(normalizedDistanceMode(cfg)) << ","
                                       << assignmentModeName(normalizedAssignmentMode(cfg)) << ","
                                       << score.euclidean_dist_m << ","
                                       << score.mahalanobis_d2 << ","
                                       << score.instant_speed_mps << ","
                                       << score.speed_diff_mps << ","
                                       << score.heading_diff_deg << ","
                                       << score.detection_speed_diff_mps << ","
                                       << score.distance_cost << ","
                                       << score.speed_cost << ","
                                       << score.heading_cost << ","
                                       << score.detection_speed_cost << ","
                                       << score.total_cost << ","
                                       << dummy_cost << ","
                                       << ((row < static_cast<int>(assignment.size()) &&
                                            assignment[row] == di) ? 1 : 0)
                                       << "\n";
                        }
                    }
                }
            }
        }
        for (int row = 0; row < row_count; ++row) {
            const int ti = active_tracks[row];
            const int col = (row < static_cast<int>(assignment.size())) ? assignment[row] : -1;
            if (col < 0 || col >= det_count ||
                (!cfg.track_allow_detection_reuse && det_used[col])) {
                continue;
            }

            ManagedTrack& tr = tracks_[ti];
            GMTIDetection& det = dets[col];
            const AssocScore& score = scores[row][col];
            if (!score.valid ||
                !costBeatsDummy(score.total_cost, dummy_cost,
                                cfg.track_allow_equal_dummy_cost)) {
                continue;
            }

            if (accepted_audit.is_open()) {
                const double tentative_scale = tr.state == TrackState::Tentative
                    ? std::max(1.0, safeNonnegative(cfg.track_tentative_gate_scale))
                    : 1.0;
                const double tentative_chi2_scale = tr.state == TrackState::Tentative
                    ? std::max(1.0, safeNonnegative(cfg.track_tentative_chi2_scale))
                    : 1.0;
                const double euclidean_gate = cfg.track_use_euclidean_gate
                    ? safeNonnegative(cfg.track_gate_m) * tentative_scale : 0.0;
                const double mahalanobis_gate = cfg.track_use_mahalanobis_gate
                    ? safeNonnegative(cfg.track_chi2_gate) * tentative_chi2_scale : 0.0;
                const double speed_gate = cfg.track_use_max_speed_gate
                    ? safeNonnegative(cfg.track_v_max) : 0.0;
                accepted_audit << std::setprecision(12)
                               << result_id << "," << tr.id << ","
                               << stateName(tr.state) << "," << col << ","
                               << tr.utc << "," << frame_utc << ","
                               << det.utcMid << ","
                               << tr.e << "," << tr.n << ","
                               << det.e << "," << det.n << ","
                               << score.euclidean_dist_m << ","
                               << euclidean_gate << ","
                               << score.mahalanobis_d2 << ","
                               << mahalanobis_gate << ","
                               << score.instant_speed_mps << ","
                               << speed_gate << ","
                               << score.total_cost << ","
                               << dummy_cost << ",1,1,1\n";
            }

            const double dt = computeDtForDetection(cfg, tr, det, frame_utc);
            double residual_d2 = 0.0;
            const TrackState previous_state = tr.state;
            kalmanUpdate(tr, det.e, det.n, measurement_var, &residual_d2);
            // Preserve the genuine online Kalman posterior before the existing
            // motion-history estimator restores measurement coordinates and
            // EN finite-difference velocity in the ManagedTrack working state.
            const double filtered_utc = validUtc(det.utcMid) ? det.utcMid : frame_utc;
            captureKinematicSnapshot(tr.filtered_snapshot, tr, filtered_utc,
                                     det.range, det.direction);
            updateMotionHistoryFromEN(tr, det, dt, cfg);

            tr.e = det.e;
            tr.n = det.n;
            tr.matched_this_frame = true;
            tr.matched_det_index = col;
            tr.age++;
            tr.hit_count++;
            tr.miss_count = 0;
            tr.consecutive_hits++;
            tr.last_result_id = result_id;
            tr.utc = validUtc(det.utcMid) ? det.utcMid : frame_utc;
            tr.state_utc = tr.utc;
            tr.range = det.range;
            tr.direction = det.direction;
            pushBounded(tr.hit_history, 1, cfg.track_confirm_window);
            pushPointHistory(tr, det.e, det.n, tr.utc, result_id, cfg);
            pushBounded(tr.residual_history, residual_d2, cfg.track_linearity_window);
            if (tr.state == TrackState::Coasted) {
                tr.state = TrackState::Confirmed;
            }
            appendTrackEvent(cfg, result_id, tr, "update", col,
                             "assigned detection");
            if (previous_state != tr.state) {
                appendTrackEvent(cfg, result_id, tr, "confirm", col,
                                 std::string("state ") + stateName(previous_state) +
                                 " -> " + stateName(tr.state));
            }

            accepted_scores[ti] = score;
            det_used[col] = 1;
            // A detection may update multiple tracks in reusable-nearest mode.
            // Keep its first associated ID in the legacy single-ID CSV column;
            // detailed track association rows retain the complete audit when
            // runtime diagnostics are enabled.
            if (det_to_track_id[col] < 0) {
                det_to_track_id[col] = tr.id;
            }
            num_matched_tracks++;

            if (cfg.track_debug_level >= 2) {
                std::cout << "[TRACK_ONLINE] assign track=" << tr.id
                          << " det=" << col
                          << " total_cost=" << score.total_cost
                          << " distance_cost=" << score.distance_cost
                          << " speed_cost=" << score.speed_cost
                          << " heading_cost=" << score.heading_cost
                          << " detection_speed_cost=" << score.detection_speed_cost
                          << " euclidean_dist_m=" << score.euclidean_dist_m
                          << " mahalanobis_d2=" << score.mahalanobis_d2
                          << " instant_speed_mps=" << score.instant_speed_mps
                          << " heading_diff_deg=" << score.heading_diff_deg
                          << " assignment_mode="
                          << assignmentModeName(normalizedAssignmentMode(cfg))
                          << " state=" << (tr.state == TrackState::Confirmed ? "Confirmed" : "Tentative")
                          << std::endl;
            }
        }
    }

    const int max_missed = std::max(0, cfg.track_max_missed);
    const int tentative_max_missed = std::max(0, cfg.track_tentative_max_missed);
    for (auto& tr : tracks_) {
        if (tr.state == TrackState::Deleted || tr.matched_this_frame) {
            continue;
        }
        const TrackState previous_state = tr.state;
        tr.age++;
        tr.miss_count++;
        tr.consecutive_hits = 0;
        pushBounded(tr.hit_history, 0, cfg.track_confirm_window);
        appendTrackEvent(cfg, result_id, tr, "miss", -1, "no assigned detection");

        if (tr.state == TrackState::Tentative) {
            if (tr.miss_count > tentative_max_missed) {
                tr.state = TrackState::Deleted;
                appendTrackEvent(cfg, result_id, tr, "delete", -1,
                                 "tentative miss limit exceeded");
                if (cfg.track_debug_level >= 2) {
                    std::cout << "[TRACK_ONLINE] delete tentative id=" << tr.id
                              << " miss=" << tr.miss_count << std::endl;
                }
            }
            continue;
        }

        if (tr.state == TrackState::Confirmed || tr.state == TrackState::Coasted) {
            if (tr.miss_count > max_missed) {
                tr.state = TrackState::Deleted;
                appendTrackEvent(cfg, result_id, tr, "delete", -1,
                                 "miss limit exceeded");
                if (cfg.track_debug_level >= 2) {
                    std::cout << "[TRACK_ONLINE] delete id=" << tr.id
                              << " miss=" << tr.miss_count << std::endl;
                }
            } else if (tr.miss_count > 0) {
                tr.state = TrackState::Coasted;
                if (previous_state != tr.state) {
                    appendTrackEvent(cfg, result_id, tr, "coast", -1,
                                     "confirmed/coasted track missed");
                }
                if (cfg.track_debug_level >= 2) {
                    std::cout << "[TRACK_ONLINE] coast id=" << tr.id
                              << " miss=" << tr.miss_count << std::endl;
                }
            }
        } else if (tr.miss_count > max_missed) {
            tr.state = TrackState::Deleted;
            appendTrackEvent(cfg, result_id, tr, "delete", -1,
                             "miss limit exceeded");
            if (cfg.track_debug_level >= 2) {
                std::cout << "[TRACK_ONLINE] delete id=" << tr.id
                          << " miss=" << tr.miss_count << std::endl;
            }
        }
    }

    int num_suppressed_coasted_speed_outliers = 0;
    for (size_t di = 0; di < dets.size(); ++di) {
        if (det_used[di]) {
            continue;
        }
        const GMTIDetection& det = dets[di];
        if (isCoastedSpeedOutlier(cfg, tracks_, det, frame_utc)) {
            ++num_suppressed_coasted_speed_outliers;
            if (cfg.track_debug_level >= 2) {
                std::cout << "[TRACK_ONLINE] suppress coasted-speed outlier det="
                          << di << std::endl;
            }
            continue;
        }
        ManagedTrack tr;
        tr.id = allocateNextId();
        tr.state = TrackState::Tentative;
        tr.e = det.e;
        tr.n = det.n;
        tr.ve = 0.0;
        tr.vn = 0.0;
        initCovarianceOnline(tr);
        tr.utc = validUtc(det.utcMid) ? det.utcMid : frame_utc;
        tr.state_utc = tr.utc;
        tr.speed = 0.0;
        tr.direction = det.direction;
        tr.range = det.range;
        tr.last_result_id = result_id;
        tr.age = 1;
        tr.hit_count = 1;
        tr.miss_count = 0;
        tr.consecutive_hits = 1;
        tr.matched_this_frame = true;
        tr.matched_det_index = static_cast<int>(di);
        // A newly initialized filter has a valid posterior equal to its first
        // measurement, but no independent pre-measurement prediction yet.
        captureKinematicSnapshot(tr.filtered_snapshot, tr, tr.utc,
                                 det.range, det.direction);
        tr.hit_history.push_back(1);
        tr.point_history.push_back(TrackPoint{det.e, det.n, tr.utc, result_id});
        tracks_.push_back(tr);
        // Newly created tracks are already matched to this frame's detection.
        // Keep the diagnostic reverse mapping consistent with the track state
        // so one-hit confirmation remains auditable in track_detections.csv.
        det_to_track_id[di] = tracks_.back().id;
        appendTrackEvent(cfg, result_id, tracks_.back(), "create",
                         static_cast<int>(di), "new tentative track");
        num_new_tracks++;

        if (cfg.track_debug_level >= 2) {
            std::cout << "[TRACK_ONLINE] new tentative id=" << tr.id
                      << " det=" << di << std::endl;
        }
    }

    for (auto& tr : tracks_) {
        if (tr.state == TrackState::Tentative && shouldPromoteToConfirmed(cfg, tr)) {
            tr.state = TrackState::Confirmed;
            appendTrackEvent(cfg, result_id, tr, "confirm",
                             tr.matched_det_index, "promotion criteria met");
            if (cfg.track_debug_level >= 2) {
                std::cout << "[TRACK_ONLINE] confirm id=" << tr.id
                          << " hits=" << recentHits(tr, cfg.track_confirm_window)
                          << "/" << cfg.track_confirm_window
                          << " linearity=" << computeLinearity(tr, cfg.track_linearity_window)
                          << std::endl;
            }
        }
    }

    std::vector<GMTIDetection> out;
    for (auto& tr : tracks_) {
        tr.is_output_this_frame = false;
    }
    for (const auto& tr : tracks_) {
        GMTIDetection det{};
        std::string failure_reason;
        if (buildProtocolDetectionForFrame(
                cfg, tr, result_id, dets, det, &failure_reason)) {
            out.push_back(det);
            appendTrackOutputPayloadAudit(
                cfg, result_id, tr,
                dets[static_cast<size_t>(tr.matched_det_index)], det);
        } else if (shouldOutputThisFrame(cfg, tr, result_id)) {
            std::cerr << "[TRACK][WARN] confirmed current match could not materialize output: "
                      << "track_id=" << tr.id
                      << " result_id=" << result_id
                      << " matched_det_index=" << tr.matched_det_index
                      << " detection_count=" << dets.size()
                      << " source=" << outputStateSourceName(
                             normalizedTrackOutputStateSource(cfg))
                      << " reason=" << failure_reason << std::endl;
        }
    }
    for (const auto& det : out) {
        ManagedTrack* tr = findTrackById(det.id);
        if (tr) {
            tr->is_output_this_frame = true;
            appendTrackEvent(cfg, result_id, *tr, "output",
                             tr->matched_det_index,
                             std::string("track selected for current output; source=") +
                                 outputStateSourceName(
                                     normalizedTrackOutputStateSource(cfg)));
        }
    }

    const int num_unmatched_detections = static_cast<int>(
        std::count(det_used.begin(), det_used.end(), 0));
    const int log_level = std::max(cfg.track_debug_level, cfg.track_debug_dump_level);
    if (log_level >= 1) {
        int num_tentative = 0;
        int num_confirmed = 0;
        int num_coasted = 0;
        int num_deleted = 0;
        for (const auto& tr : tracks_) {
            if (tr.state == TrackState::Tentative) ++num_tentative;
            if (tr.state == TrackState::Confirmed) ++num_confirmed;
            if (tr.state == TrackState::Coasted) ++num_coasted;
            if (tr.state == TrackState::Deleted) ++num_deleted;
        }
        const int active_count = static_cast<int>(tracks_.size()) - num_deleted;
        std::cout << "[TRACK] result_id=" << result_id
                  << " dets=" << dets.size()
                  << " active=" << active_count
                  << " tentative=" << num_tentative
                  << " confirmed=" << num_confirmed
                  << " coasted=" << num_coasted
                  << " deleted=" << num_deleted
                  << " outputs=" << out.size()
                  << " new=" << num_new_tracks
                  << " suppressed_coasted_speed_outliers="
                  << num_suppressed_coasted_speed_outliers
                  << " matched=" << num_matched_tracks
                  << " unmatched_dets=" << num_unmatched_detections
                  << std::endl;
        if (log_level >= 2) {
            for (const auto& tr : tracks_) {
                std::cout << "[TRACK] id=" << tr.id
                          << " state=" << stateName(tr.state)
                          << " hit=" << tr.hit_count
                          << " miss=" << tr.miss_count
                          << " recent=" << recentHits(tr, cfg.track_confirm_window)
                          << "/" << cfg.track_confirm_window
                          << " matched=" << (tr.matched_this_frame ? 1 : 0)
                          << " det=" << tr.matched_det_index
                          << " speed=" << tr.speed
                          << std::endl;
            }
        }
    }

    dumpDebugSnapshot(cfg, result_id, frame_utc, dets, det_to_track_id,
                      static_cast<int>(out.size()), num_new_tracks,
                      num_matched_tracks, num_unmatched_detections);
    writeTrackResultsCsv(cfg, result_id, tracks_);

    pruneDeleted();
    if (result_id > 0) {
        last_update_result_id_ = result_id;
    }

    std::sort(out.begin(), out.end(), [](const GMTIDetection& a, const GMTIDetection& b) {
        return a.id < b.id;
    });
    return out;
}

void TrackManager::reset()
{
    tracks_.clear();
    next_id_ = 1;
    last_update_result_id_ = 0;
    association_config_logged_ = false;
}

uint16_t TrackManager::allocateId()
{
    return allocateNextId();
}

uint16_t TrackManager::allocateNextId()
{
    uint32_t candidate = next_id_;
    for (uint32_t count = 0; count < 65535U; ++count) {
        if (candidate == 0U || candidate > 65535U) {
            candidate = 1U;
        }
        const uint16_t id = static_cast<uint16_t>(candidate);
        if (!isActiveId(id)) {
            next_id_ = static_cast<uint16_t>((candidate >= 65535U) ? 1U : candidate + 1U);
            return id;
        }
        ++candidate;
    }

    std::cout << "[TRACK][WARN] TrackManager id pool exhausted. Reusing 65535." << std::endl;
    return 65535;
}

bool TrackManager::isActiveId(uint16_t id) const
{
    for (const auto& tr : tracks_) {
        if (tr.state != TrackState::Deleted && tr.id == id) {
            return true;
        }
    }
    return false;
}

ManagedTrack* TrackManager::findTrackById(uint16_t id)
{
    for (auto& tr : tracks_) {
        if (tr.id == id) {
            return &tr;
        }
    }
    return nullptr;
}

int TrackManager::currentResultId(const Config& cfg) const
{
    if (cfg.result_file_id > 0) {
        return cfg.result_file_id;
    }
    if (!cfg.track_idx_range.empty()) {
        return cfg.track_idx_range.back();
    }
    return 0;
}

int TrackManager::maxMiss(const Config& cfg) const
{
    const int window = std::max(1, cfg.track_idx_window);
    const int truth = std::max(1, cfg.track_truth_threshold);
    return std::max(1, window - truth + 1);
}

void TrackManager::dumpDebugSnapshot(const Config& cfg,
                                     int result_id,
                                     double frame_utc,
                                     const std::vector<GMTIDetection>& detections,
                                     const std::vector<int>& det_to_track_id,
                                     int num_outputs,
                                     int num_new_tracks,
                                     int num_matched_tracks,
                                     int num_unmatched_detections) const
{
    if (!cfg.track_debug_dump) {
        return;
    }
    const std::string dir = debugDir(cfg);
    if (!ensureDirectory(dir)) {
        return;
    }

    int num_tentative = 0;
    int num_confirmed = 0;
    int num_coasted = 0;
    int num_deleted = 0;
    for (const auto& tr : tracks_) {
        switch (tr.state) {
        case TrackState::Tentative:
            ++num_tentative;
            break;
        case TrackState::Confirmed:
            ++num_confirmed;
            break;
        case TrackState::Coasted:
            ++num_coasted;
            break;
        case TrackState::Deleted:
            ++num_deleted;
            break;
        }
    }

    {
        std::ofstream frames = openCsvAppend(
            dir + "/track_frames.csv",
            "result_id,frame_utc,num_detections,num_tracks,num_tentative,num_confirmed,num_coasted,num_deleted,num_outputs,num_new_tracks,num_matched_tracks,num_unmatched_detections");
        if (frames.is_open()) {
            frames << std::setprecision(12)
                   << result_id << ","
                   << frame_utc << ","
                   << detections.size() << ","
                   << tracks_.size() << ","
                   << num_tentative << ","
                   << num_confirmed << ","
                   << num_coasted << ","
                   << num_deleted << ","
                   << num_outputs << ","
                   << num_new_tracks << ","
                   << num_matched_tracks << ","
                   << num_unmatched_detections << "\n";
        }
    }

    {
        std::ofstream dets = openCsvAppend(
            dir + "/track_detections.csv",
            "result_id,det_index,matched,matched_track_id,e,n,lat,lon,utc,range,direction");
        if (dets.is_open()) {
            for (size_t i = 0; i < detections.size(); ++i) {
                const int matched_track_id =
                    (i < det_to_track_id.size()) ? det_to_track_id[i] : -1;
                const bool matched = matched_track_id >= 0;
                dets << std::setprecision(12)
                     << result_id << ","
                     << i << ","
                     << (matched ? 1 : 0) << ","
                     << matched_track_id << ","
                     << std::setprecision(6) << detections[i].e << ","
                     << detections[i].n << ","
                     << std::setprecision(12) << detections[i].lat << ","
                     << detections[i].lon << ","
                     << detections[i].utcMid << ","
                     << detections[i].range << ","
                     << detections[i].direction << "\n";
            }
        }
    }

    {
        std::ofstream states = openCsvAppend(
            dir + "/track_states.csv",
            "result_id,track_id,state,matched_this_frame,matched_det_index,e,n,lat,lon,ve,vn,speed,heading,utc,range,direction,age,hit_count,miss_count,consecutive_hits,recent_hits,linearity,is_output");
        if (states.is_open()) {
            for (const auto& tr : tracks_) {
                double lat = 0.0;
                double lon = 0.0;
                Gaussp3RV(tr.e, tr.n, cfg.L0, lat, lon);
                const double heading = gmti::trig_lut::atan2(tr.vn, tr.ve);
                const int recent = recentHits(tr, cfg.track_confirm_window);
                const double linearity = computeLinearity(tr, cfg.track_linearity_window);
                states << std::setprecision(12)
                       << result_id << ","
                       << tr.id << ","
                       << stateName(tr.state) << ","
                       << (tr.matched_this_frame ? 1 : 0) << ","
                       << tr.matched_det_index << ","
                       << std::setprecision(6) << tr.e << ","
                       << tr.n << ","
                       << std::setprecision(12) << lat << ","
                       << lon << ","
                       << tr.ve << ","
                       << tr.vn << ","
                       << tr.speed << ","
                       << heading << ","
                       << tr.utc << ","
                       << tr.range << ","
                       << tr.direction << ","
                       << tr.age << ","
                       << tr.hit_count << ","
                       << tr.miss_count << ","
                       << tr.consecutive_hits << ","
                       << recent << ","
                       << linearity << ","
                       << (tr.is_output_this_frame ? 1 : 0) << "\n";
            }
        }
    }
}

void TrackManager::pruneDeleted()
{
    tracks_.erase(std::remove_if(tracks_.begin(), tracks_.end(),
                                 [](const ManagedTrack& tr) {
                                     return tr.state == TrackState::Deleted;
                                 }),
                  tracks_.end());
}
