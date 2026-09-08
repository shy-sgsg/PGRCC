#ifndef MAINCTRL_H
#define MAINCTRL_H

#include "PipeRW.h"
#include "PipeStruDef.h"
#include <queue>
#include <mqueue.h>
#include <stdio.h>
#include "MyFileStream.h"
#include "config_structs.hpp"
#include "trackModule.hpp"
#include "TrackManager.hpp"
#include "GMTIProcessor.hpp"
#include <vector>
#include <mutex>
#include <deque>
#include <cstdint>
#include <atomic>
#include <memory>
#include <condition_variable>

class EchoCycleAssembler;
class ShmEchoReceiver;
class PrtStreamParser;
class TestCycleByteAccumulator;
class ShmProtocolAudit;

struct GMTIResultPacket {
    std::vector<GMTIDetection> targets;
    std::vector<uint8_t> image;
    uint16_t image_rows = 0;
    uint16_t image_cols = 0;
    bool image_available = false;
    double corner_lon[5] = {0.0, 0.0, 0.0, 0.0, 0.0};
    double corner_lat[5] = {0.0, 0.0, 0.0, 0.0, 0.0};
    int result_file_id = 0;
    double dbs_out_res_m = 0.0;
    double squint_angle = 0.0;
    int squint_side = 0;
    uint64_t acquisition_cycle_id = 0;
    uint64_t config_generation = 0;
};

class MainCtrl {

public:
    MainCtrl(const Config& initialConfig,
             const std::string& configPath,
             bool enablePipes = true);
    ~MainCtrl();

    std::atomic<bool> running_{false};

    pthread_t thrRecvCmd_{};
    pthread_t thrRecvEcho_{};
    pthread_t thrProcData_{};
    pthread_t thrSendRes_{};

    // 命令接收线程
    static void* OnRecvCmdThread(void* param);
    // 回波数据接收线程
    static void* OnEchoRecvThread(void* param);
    // 共享内存接收线程：test=1 仅计数；test=2 旁路审查后计数；test=0
    // 解析 PRT 并组周期。
    static void* ShmEchoRecvThread(void* param);
    // 回波数据处理线程
    static void* ProcessDataThread(void* param);
    // 结果发送线程
    static void* OnResSendThread(void* param);

    std::atomic<bool> IsResultReady{false};

    std::atomic<uint32_t> m_workmode{Mode_INIT};
    
    char * m_SendBuf = nullptr;

    PipeRW m_RecvCmdPipe;
    PipeRW m_RecvEchoPipe;
    PipeRW m_SendResultPipe;

    bool Init();
    bool InitThread();
    void StopThreads();

    FileOnlyStream file_stream;

    void initFileStream(const std::string& filename,
                       const std::string& mode = "append") {
        file_stream.open(filename, mode);
    }

    // ===== 新增成员：GMTI 专用 =====
    Config cfg_;                           // GMTI 配置对象
    GMTIProcessor gmti_proc_;              // GMTI 处理器实例
    std::vector<std::vector<double>> all_frames_MT_;  // 所有帧的检测目标
    std::vector<int> beamList_;            // 当前扫描周期内的 beam 列表
    std::vector<GMTIOutput> beamResults_;   // 当前扫描周期内的 beam 处理结果
    std::vector<Track> current_tracks_;    // 当前航迹结果
    TrackManager track_manager_;           // 跨周期持久航迹编号管理器
    std::vector<GMTIDetection> latest_gmti_targets_; // 最新 GMTI 目标
    std::deque<GMTIResultPacket> pending_result_packets_; // 待发送结果快照
    std::string latest_result_file_;       // 最新生成的 GMTI 航迹结果文件
    
    std::string gmti_config_xml_;          // GMTI 配置 XML 文件路径
    std::string runtime_mode_override_;    // 命令行覆盖 runtime_mode，空表示使用 XML
    bool runtime_diagnostics_override_set_ = false;
    bool runtime_diagnostics_override_ = true;
    ModeSwitchCmd last_cmd_;               // 最后接收的模式切换命令
    std::mutex result_mutex_;              // 保护 latest_gmti_targets_
    mutable std::mutex config_mutex_;      // 保护配置快照 / last_cmd_
    std::string pipe_root_path_;           // 管道主路径

    // ===== 新增函数：GMTI 参数和结果处理 =====
    // 从 ModeSwitchCmd 更新 XML 文件中的参数
    bool updateXmlFromModeSwitchCmd(const ModeSwitchCmd& cmd, const std::string& xmlPath);

    // 验证 GMTI 参数的有效性
    bool validateGMTIParams();

    // 打包最新 GMTI 结果为主控协议格式
    bool packGMTIResults(const GMTIResultPacket& packet, char* buffer, size_t buffer_size, uint32_t& packed_len);

    // 本地测试模式：XML 已在进程启动时读取一次。
    // loop=true 时只重复处理 echoFiles[0]；maxCycles=0 表示持续运行，
    // 直到收到 SIGINT/SIGTERM。
    bool RunLocalTest(const std::vector<std::string>& echoFiles,
                      bool loop = false,
                      int maxCycles = 0);
    bool pipesEnabled() const { return pipes_enabled_; }
    bool isShmMode() const;
    std::shared_ptr<const Config> currentConfigSnapshot() const;
    bool applyModeSwitchCmdToRuntime(const ModeSwitchCmd& cmd);

private:
    bool initializePipes();
    bool deriveAndValidateConfig(Config& cfg, std::string& error) const;
    bool configureEchoBuffers(const Config& cfg, bool reconfigure, std::string& error);
    void printStartupConfig(const Config& cfg) const;

    uint16_t next_result_msg_count_ = 0;
    bool pipes_enabled_ = true;
    bool recv_cmd_started_ = false;
    bool recv_echo_started_ = false;
    bool proc_data_started_ = false;
    bool send_res_started_ = false;
    bool initialized_ = false;
    Config base_cfg_;
    std::shared_ptr<const Config> runtime_config_;
    std::unique_ptr<EchoCycleAssembler> cycle_assembler_;
    std::unique_ptr<TestCycleByteAccumulator> test_cycle_accumulator_;
    std::unique_ptr<ShmProtocolAudit> test_protocol_auditor_;
    std::unique_ptr<ShmEchoReceiver> shm_receiver_;
    std::shared_ptr<PrtStreamParser> prt_parser_;
    std::atomic<uint64_t> next_shm_result_id_{0};
    std::mutex echo_feed_mutex_;
    std::condition_variable echo_feed_cv_;
    std::size_t echo_feeds_in_progress_ = 0;
    bool echo_resetting_ = false;

};

#endif // MAINCTRL_H
