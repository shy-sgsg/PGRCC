#include "cfar_geometry.hpp"

#include <cstdint>
#include <iostream>
#include <vector>

#define CHECK(condition)                                                     \
    do {                                                                      \
        if (!(condition)) {                                                   \
            std::cerr << "CHECK failed at " << __FILE__ << ':' << __LINE__   \
                      << ": " #condition << '\n';                            \
            return 1;                                                         \
        }                                                                     \
    } while (false)

int main()
{
    const gmti::cfar::GeometryCounts rectangular = gmti::cfar::count_geometry(
        8, 10, 1, 1, false, -1, -1, 0, 7, 0);
    CHECK(rectangular.total_cells == 80U);
    CHECK(rectangular.edge_invalid_cells == 56U);
    CHECK(rectangular.threshold_test_count == 24U);
    CHECK(rectangular.valid_cut_count == 24U);

    const gmti::cfar::GeometryCounts excluded = gmti::cfar::count_geometry(
        8, 10, 1, 1, false, 3, 3, 0, 7, 0);
    CHECK(excluded.excluded_cells == 6U);
    CHECK(excluded.valid_cut_count == 18U);

    const gmti::cfar::GeometryCounts circular = gmti::cfar::count_geometry(
        8, 10, 1, 1, true, 0, 1, 2, 3, 1);
    CHECK(circular.threshold_test_count == 48U);
    CHECK(circular.excluded_cells == 12U);
    CHECK(circular.cut_band_filtered_cells == 36U);
    CHECK(circular.valid_cut_count == 12U);

    const gmti::cfar::GeometryCounts outside_band = gmti::cfar::count_geometry(
        8, 10, 1, 1, true, -1, -1, 2, 3, 2);
    CHECK(outside_band.valid_cut_count == 36U);

    std::vector<float> hits(12, 0.0f);
    hits[2] = 4.0f;
    hits[9] = 1.0f;
    const std::uint64_t hash_a = gmti::cfar::hash_hit_indices(hits);
    hits[9] = 7.0f;
    const std::uint64_t hash_b = gmti::cfar::hash_hit_indices(hits);
    CHECK(hash_a == hash_b);
    hits[10] = 1.0f;
    CHECK(hash_a != gmti::cfar::hash_hit_indices(hits));

    std::vector<gmti::cfar::CfarGeometryDiagnostics> diagnostic_rows;
    const auto diagnostic_pair = gmti::cfar::append_geometry_diagnostic_pair(
        diagnostic_rows, "union_dynamic_in_band", "union_full_band");
    CHECK(diagnostic_rows.size() == 2U);
    diagnostic_rows[diagnostic_pair.first].height = 130;
    diagnostic_rows[diagnostic_pair.first].valid_cut_count = 100U;
    diagnostic_rows[diagnostic_pair.second].height = 130;
    diagnostic_rows[diagnostic_pair.second].valid_cut_count = 200U;
    CHECK(diagnostic_rows[diagnostic_pair.first].branch == "union_dynamic_in_band");
    CHECK(diagnostic_rows[diagnostic_pair.first].height == 130);
    CHECK(diagnostic_rows[diagnostic_pair.first].valid_cut_count == 100U);
    CHECK(diagnostic_rows[diagnostic_pair.second].branch == "union_full_band");
    CHECK(diagnostic_rows[diagnostic_pair.second].height == 130);
    CHECK(diagnostic_rows[diagnostic_pair.second].valid_cut_count == 200U);

    std::cout << "cfar_geometry_selftest passed\n";
    return 0;
}
