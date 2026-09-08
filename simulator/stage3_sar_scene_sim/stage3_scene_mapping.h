#pragma once

#include "stage3_config.h"

namespace gmti {
namespace stage3 {

// Ground-coordinate frame shared by ROI cropping and mirror expansion.  The
// anchor is the source-image centre and the two axes define source columns and
// rows respectively.
struct Stage3SceneFrame {
    double anchor_e_m = 0.0;
    double anchor_n_m = 0.0;
    double range_axis_e = -1.0;
    double range_axis_n = 0.0;
    double azimuth_axis_e = 0.0;
    double azimuth_axis_n = -1.0;
};

struct Stage3SourceGrid {
    int width = 0;
    int height = 0;
    double center_col = 0.0;
    double center_row = 0.0;
    double pixel_size_range_m = 1.0;
    double pixel_size_azimuth_m = 1.0;
    bool col_increases_with_range = true;
    bool row_increases_with_azimuth = true;
    int offset_col = 0;
    int offset_row = 0;
};

struct Stage3SourceIndex {
    long long raw_col = 0;
    long long raw_row = 0;
    int source_col = -1;
    int source_row = -1;
    long long tile_x = 0;
    long long tile_y = 0;
    int flip_mode = 0;
    bool valid = false;
};

void stage3ComputeLook(double theta_deg, int side, double &look_e, double &look_n);

// Reflect-101 has period 2*(N-1): 0,1,...,N-1,N-2,...,1,0,... .
// Unlike endpoint-repeating reflection, neither endpoint is duplicated.
int stage3MirrorIndexReflect101(long long raw_index, int size);

Stage3SceneFrame makeStage3SceneFrame(const Stage3Config &cfg);

Stage3SourceIndex stage3MapGroundToSource(const Stage3SceneFrame &frame,
                                           const Stage3SourceGrid &source,
                                           double ground_e_m,
                                           double ground_n_m,
                                           bool mirror_outside_source);

} // namespace stage3
} // namespace gmti
