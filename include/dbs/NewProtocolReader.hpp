#ifndef NEW_PROTOCOL_READER_HPP
#define NEW_PROTOCOL_READER_HPP

#include "config_structs.hpp"

#include <cstddef>
#include <cstdint>
#include <complex>
#include <vector>

// GPU 主链路使用的新协议 PRT 块。数据保留每个 PRT 的 256 字节头
// 以及样本/通道/IQ 协议排布：文件模式一次读入连续块，共享内存模式直接
// 引用当前双缓冲周期。CPU 只解析包头，不再拷贝样本载荷、创建四份整幅
// 复数矩阵或执行通道相位补偿/合成。external_prt_data 的生命期由当前
// EchoCycleView 保证，仅在本周期并行处理完成前使用。
struct NewProtocolGpuInput {
    std::vector<std::uint8_t> payload;
    const std::uint8_t *external_prt_data = nullptr;
    std::size_t external_prt_bytes = 0;
    std::size_t pulse_count = 0;
    std::size_t samples_per_prt = 0;
    std::size_t channel_count = 0;
    std::size_t bytes_per_iq = 0;
    std::size_t header_bytes = 0;
    std::size_t prt_bytes = 0;
    int read_channel_1 = 0;
    int read_channel_2 = 0;
    int fusion_channel_3 = 0;
    int fusion_channel_4 = 0;
    bool int16_iq = false;
    bool four_channel_fusion = false;
    bool four_channel_phase_compensation_enable = true;
    double compensation_theta_deg = 0.0;
    double compensation_height_m = 0.0;

    std::size_t sampleBytesPerPrt() const
    {
        return samples_per_prt * channel_count * 2U * bytes_per_iq;
    }

    const std::uint8_t *data() const
    {
        return external_prt_data != nullptr ? external_prt_data : payload.data();
    }

    std::size_t byteSize() const
    {
        return external_prt_data != nullptr ? external_prt_bytes : payload.size();
    }

    bool valid() const
    {
        return pulse_count > 0U && samples_per_prt > 0U &&
               channel_count > 0U && (bytes_per_iq == 2U || bytes_per_iq == 4U) &&
               header_bytes > 0U && prt_bytes == header_bytes + sampleBytesPerPrt() &&
               data() != nullptr && byteSize() == pulse_count * prt_bytes;
    }
};

bool readPulseBlockNewProtocol(const Config &cfg,
                               int beamskip,
                               std::vector<std::complex<float>> &data1,
                               std::vector<std::complex<float>> &data2,
                               std::vector<double> &utc,
                               double &theta_sq,
                               std::vector<std::vector<double>> &posRaw);

bool readPulseBlockNewProtocol(const Config &cfg,
                               const EchoCycleView &cycle,
                               int beamskip,
                               std::vector<std::complex<float>> &data1,
                               std::vector<std::complex<float>> &data2,
                               std::vector<double> &utc,
                               double &theta_sq,
                               std::vector<std::vector<double>> &posRaw);

// 生产 GPU 路径：只打包原始 payload，解码、相位补偿、残余相位估计和
// 四通道合成均在 GPU 上完成。
bool readPulseBlockNewProtocolGpuInput(const Config &cfg,
                                      int beamskip,
                                      NewProtocolGpuInput &gpu_input,
                                      std::vector<double> &utc,
                                      double &theta_sq,
                                      std::vector<std::vector<double>> &posRaw);

bool readPulseBlockNewProtocolGpuInput(const Config &cfg,
                                      const EchoCycleView &cycle,
                                      int beamskip,
                                      NewProtocolGpuInput &gpu_input,
                                      std::vector<double> &utc,
                                      double &theta_sq,
                                      std::vector<std::vector<double>> &posRaw);

// GPU 不可用或设备阶段失败时的兼容回退。该函数也用于 CPU/GPU 数值 A/B，
// 正常生产主链路不会调用。
bool decodeNewProtocolGpuInputCpu(const Config &cfg,
                                  const NewProtocolGpuInput &gpu_input,
                                  std::vector<std::complex<float>> &data1,
                                  std::vector<std::complex<float>> &data2);

#endif // NEW_PROTOCOL_READER_HPP
