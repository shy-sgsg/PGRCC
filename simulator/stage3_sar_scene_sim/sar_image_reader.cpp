#include "sar_image_reader.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <limits>
#include <sstream>
#include <tiffio.h>

namespace gmti {
namespace stage3 {

namespace {

std::string nextToken(std::istream &is)
{
    std::string t;
    while (is >> t) {
        if (!t.empty() && t[0] == '#') {
            std::string dummy;
            std::getline(is, dummy);
            continue;
        }
        return t;
    }
    return "";
}

bool readPgm(const std::string &path, SarImage &image, std::string &err)
{
    std::ifstream in(path.c_str(), std::ios::binary);
    if (!in) {
        err = "failed to open SAR image " + path;
        return false;
    }
    const std::string magic = nextToken(in);
    if (magic != "P2" && magic != "P5") {
        err = "unsupported SAR image format in " + path + "; first version supports PGM P2/P5 or CSV";
        return false;
    }
    const int w = std::atoi(nextToken(in).c_str());
    const int h = std::atoi(nextToken(in).c_str());
    const int maxv = std::max(1, std::atoi(nextToken(in).c_str()));
    if (w <= 0 || h <= 0) {
        err = "invalid PGM size in " + path;
        return false;
    }
    image.width = w;
    image.height = h;
    image.original_width = w;
    image.original_height = h;
    image.value.assign(static_cast<size_t>(w) * static_cast<size_t>(h), 0.0f);
    if (magic == "P2") {
        for (size_t i = 0; i < image.value.size(); ++i) {
            const std::string tok = nextToken(in);
            if (tok.empty()) break;
            image.value[i] = static_cast<float>(std::atof(tok.c_str()) / static_cast<double>(maxv));
        }
    } else {
        in.get();
        for (size_t i = 0; i < image.value.size(); ++i) {
            unsigned char v = 0;
            in.read(reinterpret_cast<char *>(&v), 1);
            image.value[i] = static_cast<float>(static_cast<double>(v) / static_cast<double>(maxv));
        }
    }
    return true;
}

bool readCsvGrid(const std::string &path, SarImage &image, std::string &err)
{
    std::ifstream in(path.c_str());
    if (!in) {
        err = "failed to open SAR image " + path;
        return false;
    }
    std::vector<std::vector<float>> rows;
    std::string line;
    while (std::getline(in, line)) {
        for (char &c : line) {
            if (c == ',') c = ' ';
        }
        std::istringstream ss(line);
        std::vector<float> row;
        float v = 0.0f;
        while (ss >> v) row.push_back(v);
        if (!row.empty()) rows.push_back(row);
    }
    if (rows.empty()) {
        err = "empty CSV SAR grid " + path;
        return false;
    }
    const int w = static_cast<int>(rows.front().size());
    const int h = static_cast<int>(rows.size());
    image.width = w;
    image.height = h;
    image.original_width = w;
    image.original_height = h;
    image.value.assign(static_cast<size_t>(w) * static_cast<size_t>(h), 0.0f);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w && x < static_cast<int>(rows[y].size()); ++x) {
            image.value[static_cast<size_t>(y) * w + x] = rows[y][x];
        }
    }
    return true;
}

bool readTiff(const std::string &path, SarImage &image, std::string &err)
{
    TIFF *tif = TIFFOpen(path.c_str(), "r");
    if (tif == nullptr) {
        err = "failed to open TIFF SAR image " + path;
        return false;
    }
    uint32_t width = 0;
    uint32_t height = 0;
    if (TIFFGetField(tif, TIFFTAG_IMAGEWIDTH, &width) != 1 ||
        TIFFGetField(tif, TIFFTAG_IMAGELENGTH, &height) != 1 ||
        width == 0 || height == 0) {
        TIFFClose(tif);
        err = "invalid TIFF dimensions in " + path;
        return false;
    }
    const size_t count = static_cast<size_t>(width) * static_cast<size_t>(height);
    if (count / static_cast<size_t>(width) != static_cast<size_t>(height) ||
        width > static_cast<uint32_t>(std::numeric_limits<int>::max()) ||
        height > static_cast<uint32_t>(std::numeric_limits<int>::max())) {
        TIFFClose(tif);
        err = "TIFF dimensions overflow host indexing in " + path;
        return false;
    }
    std::vector<uint32_t> rgba;
    try {
        rgba.resize(count);
        image.value.resize(count);
    } catch (const std::exception &e) {
        TIFFClose(tif);
        err = std::string("failed to allocate TIFF raster: ") + e.what();
        return false;
    }
    if (TIFFReadRGBAImageOriented(tif, width, height, rgba.data(), ORIENTATION_TOPLEFT, 0) != 1) {
        TIFFClose(tif);
        err = "failed to decode TIFF raster " + path;
        return false;
    }
    TIFFClose(tif);
    image.width = static_cast<int>(width);
    image.height = static_cast<int>(height);
    image.original_width = image.width;
    image.original_height = image.height;
    for (size_t i = 0; i < count; ++i) {
        const uint32_t px = rgba[i];
        const double r = static_cast<double>(TIFFGetR(px));
        const double g = static_cast<double>(TIFFGetG(px));
        const double b = static_cast<double>(TIFFGetB(px));
        image.value[i] = static_cast<float>((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0);
    }
    return true;
}

} // namespace

bool readSarImage(const std::string &path, const std::string &input_type, SarImage &image, std::string &err)
{
    std::string lower = input_type;
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    std::string ext;
    const size_t dot = path.find_last_of('.');
    if (dot != std::string::npos) {
        ext = path.substr(dot);
        std::transform(ext.begin(), ext.end(), ext.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    }
    if (path.size() >= 4 && path.substr(path.size() - 4) == ".csv") {
        return readCsvGrid(path, image, err);
    }
    if (lower == "geotiff" || lower == "tif" || lower == "tiff" ||
        ext == ".tif" || ext == ".tiff") {
        return readTiff(path, image, err);
    }
    if (lower == "pgm" || lower == "image" || ext == ".pgm") {
        return readPgm(path, image, err);
    }
    err = "unsupported SAR image type for " + path + "; supported: TIFF/GeoTIFF, PGM, CSV";
    return false;
}

} // namespace stage3
} // namespace gmti
