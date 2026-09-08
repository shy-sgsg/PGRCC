#pragma once

#include <algorithm>
#include <cctype>
#include <string>

namespace gmti {
namespace tracking {

enum class TrackOutputStateSource {
    Measurement,
    Prediction,
    KalmanFiltered
};

inline bool canonicalizeTrackOutputStateSource(const std::string& input,
                                               std::string& canonical)
{
    canonical = input;
    std::transform(canonical.begin(), canonical.end(), canonical.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (canonical == "measurement" || canonical == "detection" ||
        canonical == "measured") {
        canonical = "measurement";
        return true;
    }
    if (canonical == "prediction" || canonical == "predict" ||
        canonical == "kalman_prediction") {
        canonical = "prediction";
        return true;
    }
    if (canonical == "kalman_filtered" || canonical == "filtered" ||
        canonical == "kalman" || canonical == "kalman_smoothed" ||
        canonical == "smoothed") {
        canonical = "kalman_filtered";
        return true;
    }
    return false;
}

inline TrackOutputStateSource trackOutputStateSourceFromCanonical(
    const std::string& canonical)
{
    if (canonical == "prediction") {
        return TrackOutputStateSource::Prediction;
    }
    if (canonical == "kalman_filtered") {
        return TrackOutputStateSource::KalmanFiltered;
    }
    return TrackOutputStateSource::Measurement;
}

inline const char* trackOutputStateSourceName(TrackOutputStateSource source)
{
    switch (source) {
    case TrackOutputStateSource::Measurement: return "measurement";
    case TrackOutputStateSource::Prediction: return "prediction";
    case TrackOutputStateSource::KalmanFiltered: return "kalman_filtered";
    }
    return "measurement";
}

} // namespace tracking
} // namespace gmti

