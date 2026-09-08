// processPeriodsParallel.cpp
// 并行派发多个 beam 到多个独立的 GMTIProcessor 实例（单 GPU，多 stream/多 workspace）

#include "GMTIProcessor.hpp"
#include "dbs/DbsFusion.hpp"
#include <thread>
#include <atomic>
#include <memory>
#include <vector>
#include <algorithm>
#include <iostream>
#include <cmath>
#include <exception>

namespace {

bool isFatalCudaError(cudaError_t error)
{
    return error == cudaErrorIllegalAddress ||
           error == cudaErrorLaunchFailure ||
           error == cudaErrorLaunchTimeout ||
           error == cudaErrorAssert ||
           error == cudaErrorUnknown;
}

} // namespace

bool GMTIProcessor::fusionWorkerLayoutMatches(const Config &cfg) const
{
    return fusion_worker_layout_valid_ &&
           fusion_worker_proc_pulse_num_ == effectivePulseNum(cfg) &&
           fusion_worker_pulse_dec_ == std::max(1, cfg.pulse_dec) &&
           fusion_worker_rg_len_ == cfg.rg_len &&
           fusion_worker_range_fft_len_ == effectiveRangeFftLen(cfg);
}

void GMTIProcessor::invalidateFusionWorkers()
{
    // Call only after all worker threads of the preceding scan have joined.
    // Destroying a worker releases its independent CUDA stream and all device
    // allocations, so a changed input layout can never reuse stale buffers.
    fusion_workers_.clear();
    fusion_worker_layout_valid_ = false;
    fusion_worker_proc_pulse_num_ = 0;
    fusion_worker_pulse_dec_ = 0;
    fusion_worker_rg_len_ = 0;
    fusion_worker_range_fft_len_ = 0;
}

size_t GMTIProcessor::fusionWorkerCountForMemory(const Config &cfg,
                                                  size_t requested_workers,
                                                  size_t free_bytes) const
{
    const size_t configured_cap = static_cast<size_t>(
        std::max(1, cfg.wavepos_parallel_max_workers));
    const size_t desired_workers = cfg.wavepos_parallel
        ? std::min(requested_workers, configured_cap)
        : 1U;
    if (desired_workers == 0U) {
        return 0U;
    }

    const size_t proc_pulse_num = static_cast<size_t>(
        std::max(1, effectivePulseNum(cfg)));
    const size_t range_fft_len = static_cast<size_t>(
        std::max(1, effectiveRangeFftLen(cfg)));
    const size_t range_len = static_cast<size_t>(std::max(1, cfg.rg_len));
    const size_t workspace_bytes =
        7U * proc_pulse_num * range_fft_len * sizeof(float) * 2U +
        range_len * sizeof(float) * 2U;
    // Keep the same per-worker reserve used by the processing path for
    // pulse compression, P38, CFAR and clustering temporaries.
    const size_t per_worker_bytes = workspace_bytes + 96U * 1024U * 1024U;
    const size_t device_reserve = std::min(
        free_bytes / 3U, static_cast<size_t>(768U * 1024U * 1024U));
    const size_t usable_bytes =
        free_bytes > device_reserve ? free_bytes - device_reserve : 0U;
    const size_t additional_workers = per_worker_bytes > 0U
        ? usable_bytes / per_worker_bytes : 0U;

    // cudaMemGetInfo excludes allocations already held by this pool. Those
    // workers can be used without a new allocation, so do not shrink a
    // stable pool simply because its own workspace made free_bytes smaller.
    const size_t retained_workers = fusionWorkerLayoutMatches(cfg)
        ? fusion_workers_.size() : 0U;
    const size_t capacity = retained_workers + additional_workers;
    return std::max<size_t>(
        1U, std::min(desired_workers, capacity));
}

bool GMTIProcessor::prewarmFusionWorkers(const Config &cfg)
{
    if (!cfg.enable_dbs_fusion) {
        return true;
    }
    size_t free_bytes = 0U;
    size_t total_bytes = 0U;
    const cudaError_t status = cudaMemGetInfo(&free_bytes, &total_bytes);
    if (status != cudaSuccess) {
        std::cerr << "[fusion][WORKER-POOL] startup cudaMemGetInfo failed: "
                  << cudaGetErrorString(status) << std::endl;
        return false;
    }
    const size_t requested_workers = cfg.wavepos_parallel
        ? static_cast<size_t>(std::max(1, cfg.wavepos_parallel_max_workers))
        : 1U;
    const size_t worker_count = fusionWorkerCountForMemory(
        cfg, requested_workers, free_bytes);
    if (!ensureFusionWorkers(worker_count, cfg)) {
        return false;
    }
    std::cout << "[fusion][WORKER-POOL] prewarm workers=" << worker_count
              << " cuda_free=" << (free_bytes / 1024U / 1024U)
              << "MB total=" << (total_bytes / 1024U / 1024U)
              << "MB" << std::endl;
    return true;
}

bool GMTIProcessor::ensureFusionWorkers(size_t required_workers,
                                        const Config &cfg)
{
    required_workers = std::max<size_t>(1U, required_workers);
    const bool layout_changed = !fusionWorkerLayoutMatches(cfg);
    if (layout_changed) {
        invalidateFusionWorkers();
        fusion_worker_proc_pulse_num_ = effectivePulseNum(cfg);
        fusion_worker_pulse_dec_ = std::max(1, cfg.pulse_dec);
        fusion_worker_rg_len_ = cfg.rg_len;
        fusion_worker_range_fft_len_ = effectiveRangeFftLen(cfg);
        fusion_worker_layout_valid_ = true;
    }

    // A lower configured worker cap must actually return the device memory;
    // retaining idle workers would defeat the memory-aware concurrency limit.
    if (fusion_workers_.size() > required_workers) {
        fusion_workers_.erase(fusion_workers_.begin() + required_workers,
                              fusion_workers_.end());
    }

    const size_t workers_before = fusion_workers_.size();
    while (fusion_workers_.size() < required_workers) {
        std::unique_ptr<GMTIProcessor> worker(new GMTIProcessor());
        if (!worker->initFFTPlans(cfg)) {
            std::cerr << "[fusion][WORKER-POOL] initFFTPlans failed for worker "
                      << fusion_workers_.size() << std::endl;
            return false;
        }
        if (!worker->initcuFFTPlans(cfg)) {
            std::cerr << "[fusion][WORKER-POOL] initcuFFTPlans failed for worker "
                      << fusion_workers_.size() << std::endl;
            return false;
        }
        fusion_workers_.push_back(std::move(worker));
    }

    if (layout_changed || workers_before != fusion_workers_.size()) {
        std::cout << "[fusion][WORKER-POOL] "
                  << (layout_changed ? "rebuild" : "resize")
                  << " workers=" << fusion_workers_.size()
                  << " process_pulse_num=" << fusion_worker_proc_pulse_num_
                  << " pulse_dec=" << fusion_worker_pulse_dec_
                  << " rg_len=" << fusion_worker_rg_len_
                  << " range_fft_len=" << fusion_worker_range_fft_len_
                  << std::endl;
    }
    return true;
}

bool GMTIProcessor::processBeamsParallel(const std::vector<int> &beamList,
                                         const Config &cfg,
                                         const std::vector<std::vector<double>> &posRaw,
                                         std::vector<GMTIOutput> &results)
{
    if (beamList.empty()) return true;

    // 查询可用显存
    size_t freeBytes = 0, totalBytes = 0;
    cudaError_t cerr = cudaMemGetInfo(&freeBytes, &totalBytes);
    if (cerr != cudaSuccess) {
        std::cerr << "cudaMemGetInfo failed: " << cudaGetErrorString(cerr) << std::endl;
        return false;
    }
    std::cout << "[parallel] GPU free/total MB: " << (freeBytes/1024/1024) << " / " << (totalBytes/1024/1024) << std::endl;

    // 每个实例所需的显存基于当前对象的 d_workspace_bytes（在 initcuFFTPlans() 后设置）
    size_t per_instance_bytes = this->d_workspace_bytes;
    if (per_instance_bytes == 0) {
        // 保守回退值（约200MB），在未初始化的情况下使用
        per_instance_bytes = 100ull * 1024ull * 1024ull;
    }

    // std::cout << "[parallel] per_instance_bytes MB: " << (per_instance_bytes/1024/1024) << std::endl;

    // 额外开销（CFAR / phase map / 临时 device malloc），按单 period 的实际峰值做保守预算
    const size_t overhead = 40ull * 1024ull * 1024ull;
    size_t per_needed = per_instance_bytes + overhead;

    size_t max_by_mem = std::max<size_t>(1, static_cast<int>(freeBytes / per_needed));
    size_t requested = beamList.size();
    // 限制并发实例数，避免过多小片段导致上下文切换，设置合理上限
    const size_t HARD_CAP = 8;
    size_t instances = std::min<size_t>({requested, max_by_mem, HARD_CAP});

    std::cout << "[parallel] requested=" << requested << " max_by_mem=" << max_by_mem
              << " hard_cap=" << HARD_CAP << " per_needed_mb=" << (per_needed/1024/1024)
              << " -> instances=" << instances << std::endl;

    if (instances == 0) instances = 1;

        // --- Compute dataset-wide squint once using DBS-like center-wavepos method.
        //     If disabled, use the XML value directly. If enabled but estimation fails,
        //     fall back to XML cfg.squint_angle; final fallback 0.
        double final_squint = 0.0;
        if (!cfg.estimate_error_angle) {
            final_squint = std::isfinite(cfg.squint_angle) ? cfg.squint_angle : 0.0;
            std::cout << "[parallel] estimate_error_angle disabled, using XML squint="
                      << final_squint << " deg" << std::endl;
        } else {
            double computed = 0.0;
            bool ok = this->computeDatasetSquintFromCenter(beamList, cfg, posRaw, computed);
            if (!ok || !std::isfinite(computed) || std::abs(computed) > 30.0) {
                // try XML-provided value
                if (std::isfinite(cfg.squint_angle)) {
                    final_squint = cfg.squint_angle;
                    std::cout << "[parallel] computeDatasetSquintFromCenter failed, using XML squint="
                              << final_squint << " deg" << std::endl;
                } else {
                    final_squint = 0.0;
                    std::cout << "[parallel] computeDatasetSquintFromCenter failed, no XML fallback, using 0 deg" << std::endl;
                }
            } else {
                final_squint = computed;
                std::cout << "[parallel] estimated squint angle = " << computed << " deg" << std::endl;
            }
        }
        std::cout << "[parallel] final squint angle set to = " << final_squint << " deg" << std::endl;

    // 创建 worker 实例
    // 如果用户在 XML 中关闭了波位并行，则改为顺序逐波位计算
    if (!cfg.wavepos_parallel) {
        std::cout << "[parallel] wavepos_parallel disabled: running sequentially" << std::endl;
        // 需要为当前实例初始化 FFT/cuFFT plans
        if (!this->initFFTPlans(cfg)) {
            std::cerr << "initFFTPlans failed for sequential processing" << std::endl;
            return false;
        }
        if (!this->initcuFFTPlans(cfg)) {
            std::cerr << "initcuFFTPlans failed for sequential processing" << std::endl;
            return false;
        }
        this->setGlobalSquint(final_squint);

        results.clear();
        results.resize(beamList.size());
        bool all_ok = true;
        for (size_t i = 0; i < beamList.size(); ++i) {
            int per = beamList[i];
            GMTIOutput out;
            bool s = this->processOnePeriod(per, cfg, posRaw, out);
            results[i] = std::move(out);
            all_ok = all_ok && s;
            std::cout << "[sequential] beam " << per << " -> " << (s?"OK":"FAIL") << std::endl;
        }
        return all_ok;
    }

    std::vector<std::unique_ptr<GMTIProcessor>> workers;
    workers.reserve(instances);
    for (size_t i = 0; i < instances; ++i) {
        workers.emplace_back(new GMTIProcessor());
        // 每个 worker 需要初始化 FFT/cuFFT plans 与显存
        std::cout << "[parallel] Initializing worker " << i << std::endl;
        if (!workers.back()->initFFTPlans(cfg)) {
            std::cerr << "Worker initFFTPlans failed for instance " << i << std::endl;
            return false;
        }
        if (!workers.back()->initcuFFTPlans(cfg)) {
            std::cerr << "Worker initcuFFTPlans failed for instance " << i << std::endl;
            return false;
        }
        // Propagate dataset-wide squint to each worker
        workers.back()->setGlobalSquint(final_squint);
        // std::cout << "[parallel] Worker " << i << " initialized (workspace bytes=" << workers.back()->d_workspace_bytes/1024/1024 << " MB)" << std::endl;
    }

    // 准备输出容器
    const size_t N = beamList.size();
    results.clear();
    results.resize(N);
    std::vector<char> ok(N, 0);

    std::atomic_size_t next_idx(0);

    // 线程池：每个 worker 对应一个线程，抢占式分配 period
    std::vector<std::thread> threads;
    threads.reserve(instances);

    for (size_t w = 0; w < instances; ++w) {
        threads.emplace_back([w, &workers, &beamList, &cfg, &posRaw, &results, &ok, &next_idx]() {
            GMTIProcessor *worker = workers[w].get();
            while (true) {
                size_t i = next_idx.fetch_add(1);
                if (i >= beamList.size()) break;
                int per = beamList[i];
                GMTIOutput out;
                bool s = worker->processOnePeriod(per, cfg, posRaw, out);
                results[i] = std::move(out);
                ok[i] = s ? 1 : 0;
                std::cout << "[parallel] beam " << per << " done by worker " << w << " -> " << (s?"OK":"FAIL") << std::endl;
            }
        });
    }

    for (auto &t : threads) if (t.joinable()) t.join();

    // 汇总结果一致性检查
    bool all_ok = std::all_of(ok.begin(), ok.end(), [](char v){return v != 0;});
    if (!all_ok) std::cerr << "Warning: some periods failed in parallel processing." << std::endl;

    return all_ok;
}

bool GMTIProcessor::processBeamsParallelFusion(const std::vector<int> &beamList,
                                               const Config &cfg,
                                               const std::vector<std::vector<double>> &posRaw,
                                               FusionGroupContext &ctx)
{
    if (beamList.empty()) return true;

    // The cached per-beam fd_ctr values are produced after the per-beam
    // Doppler-centre/theory guard.  They are therefore not an independent
    // observation of the physical squint and must not be used to estimate a
    // scan-wide pointing bias (doing so collapses the estimate toward zero).
    // Estimate once from the original centre-beam echo before starting the
    // fusion workers, using the same raw-data estimator as the non-fusion
    // parallel path.  Keep the configured squint solely as a documented
    // fallback when the data-driven estimate is unavailable.
    double estimatedFusionBiasDeg = 0.0;
    bool haveEstimatedFusionBias = false;
    if (cfg.estimate_error_angle) {
        haveEstimatedFusionBias = computeDatasetSquintFromCenter(
            beamList, cfg, posRaw, estimatedFusionBiasDeg);
        haveEstimatedFusionBias = haveEstimatedFusionBias &&
            std::isfinite(estimatedFusionBiasDeg) &&
            std::abs(estimatedFusionBiasDeg) <= 30.0;
        if (haveEstimatedFusionBias) {
            std::cout << "[fusion] raw-centre estimated beam_pointing_bias="
                      << estimatedFusionBiasDeg << " deg" << std::endl;
        } else {
            std::cerr << "[fusion][WARN] raw-centre beam-pointing estimate failed; "
                      << "will use configured squint fallback" << std::endl;
        }
    }

    ctx.reset(beamList);

    size_t freeBytes = 0, totalBytes = 0;
    cudaError_t cerr = cudaMemGetInfo(&freeBytes, &totalBytes);
    if (cerr != cudaSuccess) {
        std::cerr << "cudaMemGetInfo failed: " << cudaGetErrorString(cerr) << std::endl;
        return false;
    }

    const size_t requested = beamList.size();
    const size_t configured_cap = static_cast<size_t>(
        std::max(1, cfg.wavepos_parallel_max_workers));
    const size_t max_by_mem = fusionWorkerCountForMemory(
        cfg, requested, freeBytes);
    const size_t instances = max_by_mem;
    const size_t pulseDec = static_cast<size_t>(std::max(1, cfg.pulse_dec));
    const int procPulseNum = effectivePulseNum(cfg);
    const size_t rdRows = (static_cast<size_t>(std::max(0, procPulseNum)) + pulseDec - 1) / pulseDec;
    const size_t rdCols = static_cast<size_t>(std::max(0, cfg.rg_len));
    const double rdCacheGiB = static_cast<double>(beamList.size()) * static_cast<double>(rdRows) *
                              static_cast<double>(rdCols) * static_cast<double>(sizeof(float)) /
                              (1024.0 * 1024.0 * 1024.0);
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[fusion] group start: beams=" << beamList.size()
                  << " workers=" << instances
                  << " configured_worker_cap=" << configured_cap
                  << " memory_worker_cap=" << max_by_mem
                  << " cuda_free=" << (static_cast<double>(freeBytes) / (1024.0 * 1024.0 * 1024.0))
                  << "GiB/" << (static_cast<double>(totalBytes) / (1024.0 * 1024.0 * 1024.0))
                  << "GiB pulse_num=" << cfg.pulse_num
                  << " read_pulse_num=" << cfg.read_pulse_num
                  << " process_pulse_num=" << procPulseNum
                  << " pulse_dec=" << cfg.pulse_dec
                  << " range_input_len=" << cfg.pulse_len
                  << " range_fft_len=" << effectiveRangeFftLen(cfg)
                  << " range_crop_start=" << cfg.range_crop_start
                  << " range_output_len=" << effectiveRangeCompressLen(cfg)
                  << " rg_len=" << cfg.rg_len
                  << " estimated_dbs_amp_cache~" << rdCacheGiB << "GiB" << std::endl;
    }

    if (!ensureFusionWorkers(instances, cfg)) {
        return false;
    }
    std::vector<std::unique_ptr<GMTIProcessor>> &workers = fusion_workers_;

    std::atomic_size_t next_idx(0);
    std::atomic<int> fatal_cuda_error(static_cast<int>(cudaSuccess));
    std::vector<char> ok(beamList.size(), 0);
    std::vector<std::thread> threads;
    threads.reserve(instances);

    for (size_t w = 0; w < instances; ++w) {
        threads.emplace_back([w, &workers, &beamList, &cfg, &posRaw, &ctx, &ok,
                              &next_idx, &fatal_cuda_error]() {
            GMTIProcessor *worker = workers[w].get();
            while (true) {
                if (fatal_cuda_error.load() != static_cast<int>(cudaSuccess)) break;
                const size_t slot = next_idx.fetch_add(1);
                if (slot >= beamList.size()) break;
                const int per = beamList[slot];
                bool s = false;
                try {
                    s = worker->processOnePeriodFusionCache(
                        per, cfg, posRaw, slot, ctx);
                } catch (const std::exception& exc) {
                    std::cerr << "[fusion][CUDA-FATAL] beam=" << per
                              << " exception=" << exc.what() << std::endl;
                    fatal_cuda_error.store(static_cast<int>(cudaErrorUnknown));
                } catch (...) {
                    std::cerr << "[fusion][CUDA-FATAL] beam=" << per
                              << " unknown worker exception" << std::endl;
                    fatal_cuda_error.store(static_cast<int>(cudaErrorUnknown));
                }
                ok[slot] = s ? 1 : 0;
                if (!s) {
                    const cudaError_t worker_error = cudaGetLastError();
                    if (isFatalCudaError(worker_error)) {
                        int expected = static_cast<int>(cudaSuccess);
                        fatal_cuda_error.compare_exchange_strong(
                            expected, static_cast<int>(worker_error));
                    }
                    // A worker may already have exported its DBS cache before a
                    // later p38/detection quality gate rejects the beam. Clear
                    // that slot so partial-scan tolerance can never fuse stale
                    // or only partially processed data.
                    ctx.rd.amp[slot] = Image2D<float>();
                    ctx.rd.fd_axis[slot].clear();
                    ctx.rd.rg_axis[slot].clear();
                    ctx.meta.beams[slot] = MetaPerBeam();
                    ctx.beam_meta[slot] = FusionBeamMeta();
                    ctx.beam_meta[slot].beam_index = per;
                    ctx.beam_meta[slot].slot = static_cast<int>(slot);
                    ctx.detections[slot].clear();
                    ctx.done[slot] = 0;
                }
                // std::cout << "[fusion] period " << per << " slot " << slot
                //           << " done by worker " << w << " -> " << (s ? "OK" : "FAIL") << std::endl;
            }
        });
    }

    for (auto &t : threads) if (t.joinable()) t.join();

    const cudaError_t fatal_error =
        static_cast<cudaError_t>(fatal_cuda_error.load());
    if (fatal_error != cudaSuccess) {
        std::cerr << "[fusion][CUDA-FATAL] stop scan after device failure: "
                  << cudaGetErrorString(fatal_error) << std::endl;
        invalidateFusionWorkers();
        return false;
    }

    const size_t valid_count = static_cast<size_t>(
        std::count_if(ok.begin(), ok.end(), [](char v){ return v != 0; }));
    const size_t required_count = static_cast<size_t>(std::ceil(
        cfg.fusion_min_valid_beam_ratio * static_cast<double>(beamList.size()) - 1.0e-12));
    if (valid_count < required_count) {
        std::cerr << "[fusion][ERR] scan beam quality gate failed: valid="
                  << valid_count << '/' << beamList.size()
                  << " required=" << required_count
                  << " min_ratio=" << cfg.fusion_min_valid_beam_ratio << std::endl;
        return false;
    }
    if (valid_count != beamList.size()) {
        std::cerr << "[fusion][WARN] continuing with quality-rejected beams excluded: valid="
                  << valid_count << '/' << beamList.size() << " rejected=";
        for (size_t slot = 0; slot < beamList.size(); ++slot) {
            if (!ok[slot]) std::cerr << ' ' << beamList[slot];
        }
        std::cerr << " min_ratio=" << cfg.fusion_min_valid_beam_ratio << std::endl;
    }

    std::vector<int> activePeriods;
    std::vector<FusionBeamMeta> activeBeamMeta;
    activePeriods.reserve(beamList.size());
    activeBeamMeta.reserve(ctx.beam_meta.size());
    for (size_t slot = 0; slot < beamList.size(); ++slot) {
        if (!ok[slot] || !fusionSlotHasSignal(ctx, slot)) {
            continue;
        }
        activePeriods.push_back(beamList[slot]);
        activeBeamMeta.push_back(ctx.beam_meta[slot]);
    }
    if (activePeriods.empty()) {
        std::cerr << "[fusion] no active DBS beam after zero-fill filtering" << std::endl;
        return false;
    }
    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[fusion] active DBS beams for bias:";
        for (int p : activePeriods) {
            std::cout << ' ' << p;
        }
        std::cout << std::endl;
    }

    double biasDeg = 0.0;
    if (!cfg.estimate_error_angle) {
        biasDeg = std::isfinite(cfg.squint_angle) ? cfg.squint_angle : 0.0;
        if (cfg.runtime_diagnostics_enabled) {
            std::cout << "[fusion] estimate_error_angle disabled, using XML beam_pointing_bias="
                      << biasDeg << " deg" << std::endl;
        }
    } else {
        if (haveEstimatedFusionBias) {
            biasDeg = estimatedFusionBiasDeg;
        } else {
            biasDeg = std::isfinite(cfg.squint_angle) ? cfg.squint_angle : 0.0;
            std::cerr << "[fusion][WARN] using XML beam_pointing_bias fallback="
                      << biasDeg << " deg" << std::endl;
        }
    }
    if (!applyBeamPointingBiasToFusionContext(biasDeg, ctx)) {
        std::cerr << "[fusion] applyBeamPointingBiasToFusionContext failed" << std::endl;
        return false;
    }

    if (cfg.runtime_diagnostics_enabled) {
        std::cout << "[fusion] beam_pointing_bias=" << biasDeg << " deg" << std::endl;
    }
    return true;
}

bool GMTIProcessor::processBeamsParallelFusion(const std::vector<int> &beamList,
                                               const Config &cfg,
                                               const std::vector<std::vector<double>> &posRaw,
                                               FusionGroupContext &ctx,
                                               std::vector<GMTIOutput> &results)
{
    if (!processBeamsParallelFusion(beamList, cfg, posRaw, ctx)) {
        return false;
    }
    if (!relocateFusionDetections(ctx, cfg, results)) {
        std::cerr << "[fusion] relocateFusionDetections failed" << std::endl;
        return false;
    }
    return true;
}
