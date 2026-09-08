#include "stage3_scene_mapping.h"

#include <cmath>
#include <iostream>

namespace {

bool closeEnough(double a, double b, double tolerance = 1.0e-10)
{
    return std::abs(a - b) <= tolerance;
}

int fail(const char *message)
{
    std::cerr << "[stage3_scene_mapping_selftest][FAIL] " << message << "\n";
    return 1;
}

} // namespace

int main()
{
    using namespace gmti::stage3;

    // N=4 reflect-101 around both ends.  In particular, 3 is followed by 2
    // and 0 is followed by 1, so no endpoint is duplicated.
    const int expected[] = {3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2, 3};
    for (int raw = -3; raw <= 9; ++raw) {
        const int got = stage3MirrorIndexReflect101(raw, 4);
        if (got != expected[raw + 3]) return fail("reflect-101 reference sequence mismatch");
    }
    for (int raw = -64; raw < 64; ++raw) {
        const int a = stage3MirrorIndexReflect101(raw, 4);
        const int b = stage3MirrorIndexReflect101(raw + 1, 4);
        if (a == b) return fail("reflect-101 duplicated an endpoint");
        if (a != stage3MirrorIndexReflect101(raw + 6, 4)) {
            return fail("reflect-101 period is not 2*(N-1)");
        }
    }
    if (stage3MirrorIndexReflect101(-100, 1) != 0 ||
        stage3MirrorIndexReflect101(100, 1) != 0) {
        return fail("single-pixel reflection must remain at index zero");
    }

    Stage3Config roi_cfg;
    roi_cfg.scene.mode = "roi";
    roi_cfg.scene.roi.enabled = true;
    roi_cfg.scene.roi.beam_id_1based = 7; // deliberately not the anchor beam
    roi_cfg.tiling.reference_beam_id_1based = 31;
    roi_cfg.tiling.anchor_slant_range_m = 87900.0;

    Stage3Config mirror_cfg = roi_cfg;
    mirror_cfg.scene.mode = "mirror";
    mirror_cfg.scene.roi.enabled = false;

    const Stage3SceneFrame roi_frame = makeStage3SceneFrame(roi_cfg);
    const Stage3SceneFrame mirror_frame = makeStage3SceneFrame(mirror_cfg);
    if (!closeEnough(roi_frame.anchor_e_m, mirror_frame.anchor_e_m) ||
        !closeEnough(roi_frame.anchor_n_m, mirror_frame.anchor_n_m) ||
        !closeEnough(roi_frame.range_axis_e, mirror_frame.range_axis_e) ||
        !closeEnough(roi_frame.range_axis_n, mirror_frame.range_axis_n) ||
        !closeEnough(roi_frame.azimuth_axis_e, mirror_frame.azimuth_axis_e) ||
        !closeEnough(roi_frame.azimuth_axis_n, mirror_frame.azimuth_axis_n)) {
        return fail("ROI and mirror do not share the configured source anchor frame");
    }

    Stage3SourceGrid source;
    source.width = 9;
    source.height = 7;
    source.center_col = 4.0;
    source.center_row = 3.0;
    source.pixel_size_range_m = 0.3;
    source.pixel_size_azimuth_m = 0.3;

    const Stage3SourceIndex roi_anchor = stage3MapGroundToSource(
        roi_frame, source, roi_frame.anchor_e_m, roi_frame.anchor_n_m, false);
    const Stage3SourceIndex mirror_anchor = stage3MapGroundToSource(
        mirror_frame, source, mirror_frame.anchor_e_m, mirror_frame.anchor_n_m, true);
    if (!roi_anchor.valid || !mirror_anchor.valid ||
        roi_anchor.raw_col != 4 || roi_anchor.raw_row != 3 ||
        roi_anchor.raw_col != mirror_anchor.raw_col ||
        roi_anchor.raw_row != mirror_anchor.raw_row ||
        roi_anchor.source_col != mirror_anchor.source_col ||
        roi_anchor.source_row != mirror_anchor.source_row) {
        return fail("ROI and mirror map the shared anchor to different source pixels");
    }

    const double e = roi_frame.anchor_e_m +
                     2.0 * source.pixel_size_range_m * roi_frame.range_axis_e -
                     source.pixel_size_azimuth_m * roi_frame.azimuth_axis_e;
    const double n = roi_frame.anchor_n_m +
                     2.0 * source.pixel_size_range_m * roi_frame.range_axis_n -
                     source.pixel_size_azimuth_m * roi_frame.azimuth_axis_n;
    const Stage3SourceIndex roi_inside = stage3MapGroundToSource(roi_frame, source, e, n, false);
    const Stage3SourceIndex mirror_inside = stage3MapGroundToSource(mirror_frame, source, e, n, true);
    if (!roi_inside.valid || !mirror_inside.valid ||
        roi_inside.source_col != 6 || roi_inside.source_row != 2 ||
        roi_inside.source_col != mirror_inside.source_col ||
        roi_inside.source_row != mirror_inside.source_row) {
        return fail("ROI/mirror in-bounds coordinate mapping mismatch");
    }

    const double outside_e = roi_frame.anchor_e_m +
                             10.0 * source.pixel_size_range_m * roi_frame.range_axis_e;
    const double outside_n = roi_frame.anchor_n_m +
                             10.0 * source.pixel_size_range_m * roi_frame.range_axis_n;
    const Stage3SourceIndex roi_outside = stage3MapGroundToSource(
        roi_frame, source, outside_e, outside_n, false);
    const Stage3SourceIndex mirror_outside = stage3MapGroundToSource(
        mirror_frame, source, outside_e, outside_n, true);
    if (roi_outside.valid || !mirror_outside.valid ||
        mirror_outside.raw_col != 14 || mirror_outside.source_col != 2) {
        return fail("ROI crop and mirror expansion boundary policies are incorrect");
    }

    std::cout << "[stage3_scene_mapping_selftest] PASS: reflect-101 period/no-repeat, "
                 "shared ROI/mirror anchor, in-bounds identity, and out-of-bounds policy\n";
    return 0;
}
