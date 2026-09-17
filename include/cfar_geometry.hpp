#ifndef GMTI_CFAR_GEOMETRY_HPP
#define GMTI_CFAR_GEOMETRY_HPP

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace gmti {
namespace cfar {

// Host-side mirror of the exact CUT eligibility rules in cfar_detect_kernel.
// It is intentionally data-independent: detector output is supplied separately
// and is never reconstructed from this helper.
struct GeometryCounts {
    std::size_t total_cells = 0U;
    std::size_t edge_invalid_cells = 0U;
    std::size_t threshold_test_count = 0U;
    std::size_t excluded_cells = 0U;
    std::size_t cut_band_filtered_cells = 0U;
    std::size_t valid_cut_count = 0U;
};

inline GeometryCounts count_geometry(
    int height,
    int width,
    int guard_cells,
    int background_cells,
    bool doppler_circular,
    int exclude_row_start,
    int exclude_row_end,
    int cut_band_start,
    int cut_band_end,
    int cut_band_mode)
{
    GeometryCounts counts;
    if (height <= 0 || width <= 0) return counts;

    const int guard = std::max(0, guard_cells);
    const int background = std::max(1, background_cells);
    const int radius = guard + background;
    counts.total_cells = static_cast<std::size_t>(height) *
                         static_cast<std::size_t>(width);

    const int row_min = doppler_circular ? 0 : radius;
    const int row_max = doppler_circular ? height - 1 : height - 1 - radius;
    const int col_min = radius;
    const int col_max = width - 1 - radius;
    for (int row = 0; row < height; ++row) {
        for (int col = 0; col < width; ++col) {
            const bool edge_valid = row >= row_min && row <= row_max &&
                                    col >= col_min && col <= col_max;
            if (!edge_valid) {
                ++counts.edge_invalid_cells;
                continue;
            }
            ++counts.threshold_test_count;

            const bool excluded = exclude_row_start >= 0 &&
                                  row >= exclude_row_start &&
                                  row <= exclude_row_end;
            if (excluded) ++counts.excluded_cells;

            const bool in_cut_band = row >= cut_band_start &&
                                     row <= cut_band_end;
            const bool cut_enabled = cut_band_mode == 0 ||
                                     (cut_band_mode == 1 && in_cut_band) ||
                                     (cut_band_mode == 2 && !in_cut_band);
            if (!cut_enabled) ++counts.cut_band_filtered_cells;
            if (!excluded && cut_enabled) ++counts.valid_cut_count;
        }
    }
    return counts;
}

// Stable hash of hit-cell indices only.  Amplitudes do not affect the hash;
// this makes the value suitable for before/after diagnostic equivalence checks.
inline std::uint64_t hash_hit_indices(const std::vector<float> &hits)
{
    const std::uint64_t offset = UINT64_C(1469598103934665603);
    const std::uint64_t prime = UINT64_C(1099511628211);
    std::uint64_t hash = offset;
    for (std::size_t index = 0; index < hits.size(); ++index) {
        if (!(hits[index] > 0.0f)) continue;
        const std::uint64_t value = static_cast<std::uint64_t>(index);
        for (unsigned int byte = 0; byte < sizeof(value); ++byte) {
            hash ^= (value >> (byte * 8U)) & UINT64_C(0xff);
            hash *= prime;
        }
    }
    return hash;
}

struct CfarGeometryDiagnostics {
    int schema_version = 1;
    int period_id = -1;
    int beam_id = -1;
    std::string branch;
    int height = 0;
    int width = 0;
    int guard_cells = 0;
    int background_cells = 0;
    bool doppler_circular = false;
    int exclude_row_start = -1;
    int exclude_row_end = -1;
    int cut_band_start = 0;
    int cut_band_end = -1;
    int cut_band_mode = 0;
    std::size_t total_cells = 0U;
    std::size_t edge_invalid_cells = 0U;
    std::size_t excluded_cells = 0U;
    std::size_t cut_band_filtered_cells = 0U;
    std::size_t threshold_test_count = 0U;
    std::size_t valid_cut_count = 0U;
    std::size_t hit_cut_count = 0U;
    std::uint64_t hit_index_hash = 0U;
    bool hit_hash_available = false;
    float configured_pfa = 0.0f;
    float alpha = 0.0f;
    std::string cfar_type;
};

} // namespace cfar
} // namespace gmti

#endif
