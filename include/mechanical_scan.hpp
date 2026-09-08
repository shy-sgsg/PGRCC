#pragma once

#include "config_structs.hpp"

#include <string>
#include <vector>

// Parse only PRT headers. IQ payload remains in the source file/EchoCycleView.
bool readMechanicalPulseMeta(const Config& cfg,
                             std::vector<PulseMeta>& pulse_meta,
                             std::string* error = nullptr);

// Segment the physical servo-angle stream into scans and CPI windows. The
// runtime retains invalid windows for diagnostics and separately lists the
// windows that are safe to process.
bool makeMechanicalCpiList(const std::vector<PulseMeta>& pulse_meta,
                           const MechanicalScanConfig& cfg,
                           MechanicalScanRuntime& runtime,
                           std::string* error = nullptr);

// Build and attach one immutable runtime snapshot to cfg, then write the PRT
// and CPI audit CSV files below cfg.result_add.
bool prepareMechanicalScanRuntime(Config& cfg, std::string* error = nullptr);

bool writeMechanicalScanDiagnostics(const Config& cfg,
                                    const MechanicalScanRuntime& runtime,
                                    std::string* error = nullptr);

bool writeMechanicalDetectionResults(
    const Config& cfg,
    const std::vector<GMTIOutput::DetectionCsvRecord>& detections,
    std::string* error = nullptr);

const MechanicalCpiWindow* mechanicalWindow(const Config& cfg,
                                             int runtime_window_index);
