#pragma once

#include <string>
#include <vector>

namespace gmti {
namespace stage3 {

struct SarImage {
    int width = 0;
    int height = 0;
    int original_width = 0;
    int original_height = 0;
    int crop_col_start = 0;
    int crop_row_start = 0;
    std::vector<float> value;
};

bool readSarImage(const std::string &path, const std::string &input_type, SarImage &image, std::string &err);

} // namespace stage3
} // namespace gmti
