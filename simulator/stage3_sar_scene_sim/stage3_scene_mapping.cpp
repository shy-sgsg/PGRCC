#include "stage3_scene_mapping.h"

#include <algorithm>
#include <cmath>

namespace gmti {
namespace stage3 {

namespace {

const double kPi = 3.14159265358979323846;

long long floorDiv(long long a, long long b)
{
    long long q = a / b;
    const long long r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) --q;
    return q;
}

long long positiveMod(long long a, long long b)
{
    const long long r = a % b;
    return r < 0 ? r + b : r;
}

} // namespace

void stage3ComputeLook(double theta_deg, int side, double &look_e, double &look_n)
{
    // Match makeAlgorithmLookVectorEN(): the platform flies north and side=1
    // is left-looking. Positive scan angle therefore points aft (south).
    const double sin_a = std::sin(theta_deg * kPi / 180.0);
    const double cross = std::sqrt(std::max(0.0, 1.0 - sin_a * sin_a));
    look_e = (side >= 0 ? -cross : cross);
    look_n = -sin_a;
}

int stage3MirrorIndexReflect101(long long raw_index, int size)
{
    if (size <= 1) return 0;
    const long long period = 2LL * static_cast<long long>(size - 1);
    const long long r = positiveMod(raw_index, period);
    return static_cast<int>(r < size ? r : period - r);
}

Stage3SceneFrame makeStage3SceneFrame(const Stage3Config &cfg)
{
    Stage3SceneFrame frame;
    const int reference_beam = std::max(1, std::min(cfg.system.beam_count,
                                                    cfg.tiling.reference_beam_id_1based)) - 1;
    const double theta_deg = cfg.system.scan_min_deg +
                             cfg.system.scan_step_deg * reference_beam;
    stage3ComputeLook(theta_deg, cfg.system.squint_side,
                      frame.range_axis_e, frame.range_axis_n);
    frame.azimuth_axis_e = -frame.range_axis_n;
    frame.azimuth_axis_n = frame.range_axis_e;

    const double height_m = cfg.system.platform_height_m - cfg.scene.ground_z_m;
    const double anchor_ground_range_m = std::sqrt(std::max(
        0.0, cfg.tiling.anchor_slant_range_m * cfg.tiling.anchor_slant_range_m -
             height_m * height_m));
    frame.anchor_e_m = anchor_ground_range_m * frame.range_axis_e;
    frame.anchor_n_m = anchor_ground_range_m * frame.range_axis_n;
    return frame;
}

Stage3SourceIndex stage3MapGroundToSource(const Stage3SceneFrame &frame,
                                           const Stage3SourceGrid &source,
                                           double ground_e_m,
                                           double ground_n_m,
                                           bool mirror_outside_source)
{
    Stage3SourceIndex mapped;
    if (source.width <= 0 || source.height <= 0 ||
        !(source.pixel_size_range_m > 0.0) ||
        !(source.pixel_size_azimuth_m > 0.0)) {
        return mapped;
    }

    const double de = ground_e_m - frame.anchor_e_m;
    const double dn = ground_n_m - frame.anchor_n_m;
    const double u_m = de * frame.range_axis_e + dn * frame.range_axis_n;
    const double v_m = de * frame.azimuth_axis_e + dn * frame.azimuth_axis_n;
    const double col_dir = source.col_increases_with_range ? 1.0 : -1.0;
    const double row_dir = source.row_increases_with_azimuth ? 1.0 : -1.0;
    mapped.raw_col = static_cast<long long>(std::llround(
        source.center_col + col_dir * u_m / source.pixel_size_range_m)) +
        source.offset_col;
    mapped.raw_row = static_cast<long long>(std::llround(
        source.center_row + row_dir * v_m / source.pixel_size_azimuth_m)) +
        source.offset_row;

    if (!mirror_outside_source) {
        if (mapped.raw_col < 0 || mapped.raw_col >= source.width ||
            mapped.raw_row < 0 || mapped.raw_row >= source.height) {
            return mapped;
        }
        mapped.source_col = static_cast<int>(mapped.raw_col);
        mapped.source_row = static_cast<int>(mapped.raw_row);
        mapped.valid = true;
        return mapped;
    }

    mapped.source_col = stage3MirrorIndexReflect101(mapped.raw_col, source.width);
    mapped.source_row = stage3MirrorIndexReflect101(mapped.raw_row, source.height);
    mapped.tile_x = source.width <= 1 ? 0 : floorDiv(mapped.raw_col, source.width - 1);
    mapped.tile_y = source.height <= 1 ? 0 : floorDiv(mapped.raw_row, source.height - 1);
    const bool flip_x = positiveMod(mapped.tile_x, 2) != 0;
    const bool flip_y = positiveMod(mapped.tile_y, 2) != 0;
    mapped.flip_mode = (flip_x ? 1 : 0) + (flip_y ? 2 : 0);
    mapped.valid = true;
    return mapped;
}

} // namespace stage3
} // namespace gmti
