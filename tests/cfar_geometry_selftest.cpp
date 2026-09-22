#include "cfar_geometry.hpp"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

int main()
{
    const gmti::cfar::GeometryCounts rectangular = gmti::cfar::count_geometry(
        8, 10, 1, 1, false, -1, -1, 0, 7, 0);
    assert(rectangular.total_cells == 80U);
    assert(rectangular.edge_invalid_cells == 56U);
    assert(rectangular.threshold_test_count == 24U);
    assert(rectangular.valid_cut_count == 24U);

    const gmti::cfar::GeometryCounts excluded = gmti::cfar::count_geometry(
        8, 10, 1, 1, false, 3, 3, 0, 7, 0);
    assert(excluded.excluded_cells == 6U);
    assert(excluded.valid_cut_count == 18U);

    const gmti::cfar::GeometryCounts circular = gmti::cfar::count_geometry(
        8, 10, 1, 1, true, 0, 1, 2, 3, 1);
    assert(circular.threshold_test_count == 48U);
    assert(circular.excluded_cells == 12U);
    assert(circular.cut_band_filtered_cells == 36U);
    assert(circular.valid_cut_count == 12U);

    const gmti::cfar::GeometryCounts outside_band = gmti::cfar::count_geometry(
        8, 10, 1, 1, true, -1, -1, 2, 3, 2);
    assert(outside_band.valid_cut_count == 36U);

    std::vector<float> hits(12, 0.0f);
    hits[2] = 4.0f;
    hits[9] = 1.0f;
    const std::uint64_t hash_a = gmti::cfar::hash_hit_indices(hits);
    hits[9] = 7.0f;
    const std::uint64_t hash_b = gmti::cfar::hash_hit_indices(hits);
    assert(hash_a == hash_b);
    hits[10] = 1.0f;
    assert(hash_a != gmti::cfar::hash_hit_indices(hits));

    std::cout << "cfar_geometry_selftest passed\n";
    return 0;
}
