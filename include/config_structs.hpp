#ifndef CONFIG_STRUCTS_HPP
#define CONFIG_STRUCTS_HPP

#include <string>
#include <vector>
#include <complex>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>

// 常量定义
const double EARTH_R = 6371.0;  // 地球半径，单位：km
const double C = 299792458.0;   // 光速，单位：m/s

// A non-owning view of one complete raw echo scan. The owning pipe cycle
// buffer remains in PROCESSING state for the whole algorithm call.
struct EchoCycleView {
    const uint8_t* data = nullptr;
    std::size_t size = 0;
    std::size_t prt_bytes = 0;
    std::uint64_t acquisition_cycle_id = 0;
    bool partial_scan = false;
    int first_scan_beam = 0;
    std::size_t scan_beam_count = 0;
    std::size_t missing_leading_prt_count = 0;

    bool valid() const {
        return data != nullptr && size > 0 && prt_bytes > 0 &&
               (size % prt_bytes) == 0;
    }
};

enum class ScanMode {
    Electronic = 0,
    Mechanical = 1
};

inline const char* scanModeName(ScanMode mode)
{
    return mode == ScanMode::Mechanical ? "mechanical" : "electronic";
}

// One immutable observation for every physical PRT.  Mechanical processing
// currently consumes the centre sample as its scalar reference angle, while
// retaining this full sequence for diagnostics and future pulse-wise motion
// compensation.
struct PulseMeta {
    double utc = 0.0;
    double lat_deg = 0.0;
    double lon_deg = 0.0;
    double height_m = 0.0;
    double velocity_e_mps = 0.0;
    double velocity_n_mps = 0.0;
    double velocity_u_mps = 0.0;
    double heading_deg = 0.0;
    double servo_azimuth_deg = 0.0;
    double servo_elevation_deg = 0.0;
    double scan_center_azimuth_deg = 0.0;
    double scan_center_elevation_deg = 0.0;
    double scan_speed_az_deg_s = 0.0;
    double scan_extent_az_deg = 0.0;
    std::uint32_t prt_counter = 0;
};

struct MechanicalCpiWindow {
    int scan_id = 0;
    int window_id = 0;
    std::size_t pulse_start = 0;
    std::size_t pulse_count = 0;
    double utc_start = 0.0;
    double utc_center = 0.0;
    double utc_end = 0.0;
    double az_start_deg = 0.0;
    double az_center_deg = 0.0;
    double az_end_deg = 0.0;
    double elevation_center_deg = 0.0;
    double az_span_deg = 0.0;
    int scan_direction = 0;
    bool valid = true;
    std::string invalid_reason;
};

struct MechanicalScanConfig {
    // Fixed-size SHM acquisition boundary for one full physical sweep. File
    // input derives the count from file bytes. Zero is valid unless SHM input
    // is selected, where an explicit boundary is required.
    int acquisition_scan_prt_count = 0;
    int cpi_pulse_count = 130;
    int cpi_step_pulse = 130;
    double max_cpi_angle_span_deg = 2.0;
    bool use_actual_servo_angle = true;
    bool allow_scan_reverse = true;
    double scan_edge_guard_deg = 0.0;
    bool enable_cpi_dedup = true;
    double dedup_time_gate_s = 0.15;
    double dedup_position_gate_m = 50.0;
    double dedup_velocity_gate_mps = 5.0;
    double scan_direction_deadband_deg = 0.05;
    int scan_direction_confirm_pulses = 3;
    // Receive phase-centre geometry for a mechanically rotated whole
    // platform/receive array.  The local channel offsets are rotated about
    // the local vertical axis by mount_angle + sign*servo_azimuth.  Keeping
    // this explicit prevents the mechanical path from silently reusing the
    // electronic-scan assumption that the baseline is always along velocity.
    bool phase_center_rotation_enable = true;
    double phase_center_mount_angle_deg = 0.0;
    int phase_center_rotation_sign = 1;
};

struct MechanicalScanRuntime {
    std::vector<PulseMeta> pulse_meta;
    // Includes rejected windows so the manifest is a complete audit record.
    std::vector<MechanicalCpiWindow> windows;
    // Indices into windows for windows that may enter the processing chain.
    std::vector<int> processing_window_indices;
};

// 配置结构体，存储从 XML 读取的所有配置参数
struct Config {
    // Missing <scan_mode> intentionally preserves all historical electronic
    // wave-position behavior.
    ScanMode scan_mode = ScanMode::Electronic;
    MechanicalScanConfig mechanical_scan;
    std::shared_ptr<const MechanicalScanRuntime> mechanical_scan_runtime;
    // Actual servo azimuth of the CPI being processed.  It is populated from
    // the centre PRT/window before CTDR/P38 theory or exact-path inversion is
    // evaluated.  NaN keeps electronic/legacy callers on their old path.
    double ctdr_servo_azimuth_deg =
        std::numeric_limits<double>::quiet_NaN();

    // 文件路径相关
    std::string GMTI_Data_add;   // GMTI 数据路径1
    std::string GMTI_Data_add2;  // GMTI 数据路径2
    std::string GMTI_Data_new;   // 新协议 GMTI 数据路径
    std::string result_add;      // 结果文件夹路径
    // 是否向主控结果 FIFO 回传协议包。关闭时仍完整执行算法、落盘 GMTI/DBS/
    // 航迹产品，只是不排队也不发送主控结果包。
    bool enable_result_return = true;
    // test=1/2 由共享内存有效字节凑满一周期后处理的预准备单周期文件。
    // XML 用 ';' 分隔；once=依次各处理一次，loop=按顺序循环处理。
    std::vector<std::string> test_cycle_data_paths;
    std::string test_cycle_data_playback = "once";
    // 总体主控联调开关：0=解析主控共享内存中的真实回波并进入算法；
    // 1=payload 保持不透明，仅按成功读取的有效字节累计周期；2=在 1 的
    // 基础上旁路审查 PRT 协议。test=1/2 都只处理预准备文件，不会把共享
    // 内存 payload 送入算法。
    int test_mode = 0;
    std::string pipe_root_path = "/home/shy/pipe_test"; // POSIX 管道主路径
    std::string echo_input_mode = "file"; // file: echo FIFO path; shm: shmdemo byte ring
    // 现场主控创建的 POSIX 对象。POSIX shm_open 需要保留前导 '/'.
    std::string shm_name = "/time_seq_shm";
    std::size_t shm_read_chunk_bytes = 1024U * 1024U;
    int shm_reconnect_ms = 1000;
    int shm_idle_sleep_us = 100;
    int shm_cycle_timeout_ms = 0; // <=0 derives a conservative value from PRF
    int shm_expected_prt_mode = 9; // FPGA PRT WAGMTI mode, not command workMode
    int shm_prt_counter_phase = 0; // reliable cycle start: counter % prts_per_cycle
    int shm_scan_beam_count = 0; // 采集扫描连续波位数；0=由首波位和 wavepos_ed 推导
    bool shm_allow_partial_first_scan = false; // 从中间波位启动时处理首个扫描尾段
    std::uint64_t config_generation = 1;
    EchoCycleView echo_cycle_view; // empty in file input mode
    std::string Plane_POS_add;   // 飞机位置文件路径
    std::string CAR_POS_add;     // 车辆位置文件路径
    std::string reffunc_add;     // 参考函数文件路径
    std::string channel_mode;    // 通道模式
    std::string iq_compose;      // IQ 组合方式
    std::string iq_data_type = "float32"; // 原始 IQ 数据类型：float32 / int16
    int new_protocol_channel_count = 2;    // 新协议交织通道总数
    int new_protocol_read_channel_1 = 1;   // 新协议读取通道1（1-based）
    int new_protocol_read_channel_2 = 2;   // 新协议读取通道2（1-based）
    bool new_protocol_gpu_preprocess = true; // 原始 payload 在 GPU 解码/合成；false 仅用于兼容/A-B
    bool enable_four_channel_fusion = false; // false=ch1/ch2; true=(ch1+ch3)/(ch2+ch4)
    // Controls the existing ch1/ch3 and ch2/ch4 geometric/residual phase
    // alignment before coherent averaging. Default preserves production.
    bool four_channel_phase_compensation_enable = true;
    int four_channel_fusion_channel_3 = 3;
    int four_channel_fusion_channel_4 = 4;
    int four_channel_fusion_squint_side = 1; // 1=left-looking, 0=right-looking
    int four_channel_carrier_phase_sign = -1;
    // Protocol channel phase-centre offsets in platform-local coordinates:
    // x along-track, y right/east cross-track for the test geometry, z up.
    std::array<std::array<double, 3>, 4> four_channel_offsets_m{{
        {{-0.085, 0.0, 0.0}}, {{0.085, 0.0, 0.0}},
        {{-0.085, 0.0, 0.0}}, {{0.085, 0.0, 0.0}}
    }};
    int new_protocol_file_first_beam = 1;  // 子集文件中第一个物理波位号（1-based）
    int new_protocol_file_scan_beam_count = 0; // 多周期文件每周期连续波位数；0=处理范围
    int new_protocol_file_period_index = 0; // 多周期文件中读取的周期（0-based）
    double new_protocol_velocity_scale = 1.0; // 包头 vn/ve/vd 转为 m/s 的倍率
    // New-protocol velocity is reconstructed from the held geodetic samples
    // by default.  Header vn/ve/vd are optional and must never become an
    // implicit zero-speed source when a deployment omits those fields.
    std::string new_protocol_velocity_source = "position_delta"; // header / position_delta
    // 本地旧协议回放可选读取 RawData 同级 DBS_parameter_ID*.xml 中的逐周期
    // squint_angle；默认关闭，避免改变现网或普通文件输入的既有配置语义。
    bool legacy_replay_use_companion_parameters = false;
    std::vector<int> track_idx_range; // 航迹关联读取的周期索引
    int result_file_id = -1;     // 当前检测结果固定写入 GMTIxx.bin 的 xx，<=0 时才使用自动编号

    // 配置标志
    int INFO_Type;              // FPGA包头协议类型，1为新协议，0为旧协议
    int isPC;                   // 是否为PC，0表示不是，1表示是
    int hasRefFunc = 0;

    // 配置参数
    int info_len;               // 信息包长度
    int pulse_len;              // 脉冲长度
    int rg_len;                 // 距离采样长度
    int pulse_num;              // 脉冲数目
    int read_pulse_num = 0;     // 新协议实际读取脉冲数，<=0 表示读取 pulse_num
    int read_pulse_offset = -1; // 新协议读取起始偏移，<0 表示居中读取
    int process_pulse_num = 0;  // 当前处理矩阵脉冲数，<=0 表示使用 pulse_num
    int range_compress_len = 0; // 脉压/抽取后距离点数，>0 时覆盖 rg_len
    int range_fft_len = 0;      // 距离脉压 FFT 长度，<=0 表示使用 pulse_len
    int range_crop_start = 0;   // 距离脉压 IFFT 后截取起点，0-based
    std::string range_compression_window = "none"; // none / rect / kaiser；none 保持旧结果
    double range_compression_kaiser_beta = 4.0;
    double range_compression_bandwidth_scale = 1.0; // 相对于发射 LFM Br 的频域支撑
    bool range_compression_window_normalize = true; // 保持平均带内相干增益
    // Slow-time window before azimuth FFT; none preserves old XML behavior.
    std::string azimuth_fft_window = "none"; // none / hann / blackman_harris
    bool azimuth_fft_window_normalize = true; // preserve coherent main-peak gain
    int pulse_dec;              // 脉冲压缩比例
    double fc;                  // 中心频率（内部单位 Hz；XML 输入单位 GHz）
    double Br;                  // 带宽（MHz）
    double fs;                  // 采样频率（内部单位 Hz；XML 输入单位 MHz）
    double Tr;                  // 脉冲宽度（秒）
    double PRF;                 // 脉冲重复频率（Hz）
    bool has_sample_delay_us = false; // XML 显式提供采样/接收延迟时为 true
    double sample_delay_us = 0.0;      // 统一为 us；未提供时 dump 输出 null
    int az_count;               // 方位角计数
    double beamwidth_deg = 3.0; // 波束宽度，对应 XML boshu
    double loc_beam_gate_deg = -1.0; // 定位后波束方向门限半宽，<=0 时按 boshu 推导
    int week;                   // 周数
    double d_channel;           // 两个物理接收天线相位中心的沿航迹间距（米）
    double pf;                  // 固定参数
    double R_min;               // 最小距离
    double L0;                  // 参考经度
    double MT_nowz;             // 参考高度
    double calib_coef = 1.0;    // 校准系数
    int secBias;                // 偏差
    int skip_az_num;            // 跳过的方位脉冲数
    int Loc = 1;                // 是否使用飞机位置，1表示使用，0表示不使用

    // 新增参数
    int wavepos_st = 0;        // 波形位置起始索引
    int wavepos_ed = 25;        // 波形位置结束索引
    int wavepos_skip = 1;       // 波形位置跳过数
    int min_points = 11;        // 最小点数
    int min_len = 1;            // 最小轨迹长度

    // 跟踪输入帧顺序与调试控制
    bool mt_sort_by_utc = true; // true: 按 utc 重排; false: 保持原始 idx_range 顺序
    int track_debug_level = 0;  // 0: 关闭, 1: 输入帧摘要, 2: 关联细节
    int track_debug_frames = 5; // 仅打印前 N 帧调试信息, <=0 表示全部
    int track_debug_points = 3; // 每帧打印前 N 个点
    int track_idx_window = 4;   // 生成 track_idx_range 的窗长
    int track_truth_threshold = 3; // 判真阈值
    // 在线 TrackManager 关联模式。旧滑窗 trackModule() 不使用这些模式开关。
    int track_distance_mode = 1;   // 0=Euclidean, 1=MahalanobisSquared
    // 默认使用全局边代价最小的最近邻贪心关联，避免大规模虚警下 Hungarian
    // 矩阵求解的立方复杂度。1=Hungarian 仅供离线 A/B 或专项验证显式启用。
    int track_assignment_mode = 0; // 0=GreedyNearestNeighbor, 1=Hungarian
    // 在线输出载荷的位置状态来源：measurement / prediction / kalman_filtered。
    // 不改变“仅输出本周期实际关联的 Confirmed 航迹”这一报文筛选规则。
    std::string track_output_state_source = "measurement";
    bool track_use_distance_cost = true;
    bool track_use_speed_cost = true;
    bool track_use_heading_cost = true;
    bool track_use_detection_speed_cost = false;
    double track_distance_weight = 1.0;
    double track_detection_speed_weight = 0.0;
    bool track_use_euclidean_gate = true;
    bool track_use_mahalanobis_gate = true;
    bool track_use_max_speed_gate = true;
    bool track_use_heading_gate = false;
    bool track_use_detection_speed_gate = false;
    double track_gate_m = 300.0; // 欧氏物理距离门限，m
    double track_v_max = 100.0;  // 瞬时速度门限，m/s
    // 当量测靠近 Coasted 航迹但仅因超过 track_v_max 被拒绝时，不将其
    // 误建为新航迹；用于抑制断续航迹附近的定位跳变假警。
    bool track_suppress_coasted_speed_outlier = false;
    double track_heading_gate_deg = 90.0;
    double track_detection_speed_gate_mps = 30.0;
    double track_euclidean_cost_scale_m = 0.0;
    double track_mahalanobis_cost_scale = 0.0;
    double track_speed_cost_scale_mps = 0.0;
    double track_heading_cost_scale_deg = 180.0;
    double track_detection_speed_cost_scale_mps = 0.0;
    int track_confirm_window = 3; // 在线航迹 n屏选m确认窗口 N
    int track_confirm_hits = 2;   // 在线航迹 n屏选m确认命中数 M
    int track_max_missed = 2;     // Confirmed/Coasted 最大连续漏检保留帧数
    int track_tentative_max_missed = 1; // Tentative 最大连续漏检保留帧数
    double track_default_dt = 1.0; // UTC 无效时默认帧间隔，单位秒
    double track_chi2_gate = 9.21; // 二维量测 99% Mahalanobis 门限
    double track_tentative_gate_scale = 1.5; // Tentative 空间门限放宽倍率
    double track_tentative_chi2_scale = 1.5; // Tentative Mahalanobis 门限放宽倍率
    double track_dummy_cost = 1.5; // 真实匹配必须优于该漏检代价
    double track_invalid_cost = 1.0e12; // 有限大无效代价，避免 Hungarian 使用 inf
    bool track_allow_equal_dummy_cost = false;
    // 仅适用于 GreedyNearestNeighbor：允许同一周期检测更新多个既有航迹。
    // 默认严格一对一，避免同一个量测把两个航迹同时确认成协议目标。开启该项
    // 时 Hungarian 会自动退化为贪心模式，因为 Hungarian 的一对一约束与该
    // 语义不兼容。
    bool track_allow_detection_reuse = false;
    int track_linearity_window = 5; // 直线度评价窗口
    double track_min_linearity_confirm = 0.65; // Tentative 确认最小直线度
    double track_speed_smooth_weight = 0.0; // EN 差分速度平滑代价权重
    double track_heading_weight = 0.0; // EN 差分航向平滑代价权重
    // 小位移下航向由位置噪声主导。<=0 保持旧行为；>0 时只有相邻量测
    // 位移达到该门限才建立/使用航向历史。
    double track_heading_min_displacement_m = 0.0;
    double track_process_noise_pos = 25.0; // Kalman 位置过程噪声方差项
    double track_process_noise_vel = 10.0; // Kalman 速度过程噪声方差项
    double track_measurement_noise_pos = 50.0; // Kalman 位置量测噪声标准差
    // 最小可视化快照：track_frames/track_detections/track_states 三个 CSV。
    // release 默认保留；完整关联审计仍由 runtime_diagnostics_enabled 控制。
    bool track_debug_dump = true;
    std::string track_debug_dir = ""; // 为空时使用 result_add/track_debug
    int track_debug_dump_level = 1; // 1: 周期摘要/CSV, 2: 逐航迹日志
    std::string runtime_mode = "release"; // 默认实时运行；debug=输出可追溯诊断
    bool runtime_diagnostics_enabled = false; // 调试/评估时通过运行模式或命令行显式开启
    // release 下按需保留逐扫描检测 CSV，不开启全局运行时诊断及其附加产物。
    bool detection_results_csv_dump = false;
    // Stage2 provenance fields are optional and affect only local diagnostic
    // CSVs; they are never part of either external binary protocol.
    int stage2_random_seed = -1;
    std::string stage2_case_id;
    // Optional external period label for one-file-per-period Stage2 replay.
    // It never changes how a new-protocol file is read.
    int stage2_period_id = -1;
    bool low_radial_velocity_filter_enabled = false;
    double low_radial_velocity_threshold_mps = 1.0;
    // 对当前 CTDR/P38 几何模型，物理一致的 P38 斜率应为负。
    // 该开关只用于剔除已知的正斜率强伪峰；默认关闭以保持旧配置兼容。
    bool p38_slope_upper_bound_filter_enabled = false;
    double p38_slope_upper_bound_rad_per_hz = 5.0e-4;
    // 将相邻波位对同一物理散射点的重复检测合并，避免其在跟踪器中
    // 作为第二条量测新建 Tentative 航迹。仅跨相邻、不同波位生效。
    bool adjacent_beam_duplicate_suppression_enabled = true;
    double adjacent_beam_duplicate_position_gate_m = 30.0;
    double adjacent_beam_duplicate_velocity_gate_mps = 0.35;
    bool debug_pc_peak = false; // P1.5: 输出脉压后、对消前单点峰值检查
    std::string pc_peak_scene_truth; // 可选 scene_truth.csv 路径；为空时按 result_add 推断
    bool motion_comp_enable = false; // 运行单周期相位/多普勒速度诊断求解器
    // 默认严禁单周期速度解污染定位：位置由 p38 反查/精确 CTDR 模型给出，
    // 最终速度由 TrackManager 使用多周期 EN 位移/实际时间给出。
    // 仅为重现历史算法或专项研究时显式设为 true。
    bool motion_comp_apply_to_localization = false;
    bool motion_comp_analytic_enable = true; // true: 使用解析式运动补偿；false: 回退旧定位
    bool motion_comp_use_row_doppler = true; // true: af_total 使用检测所在 Doppler row
    std::string motion_comp_solver = "analytic"; // old / iterative / analytic / root1d / debug
    int motion_comp_iter = 8; // 运动补偿迭代次数
    double motion_comp_iter_tol_mps = 1.0e-4; // 迭代收敛阈值
    // P38 多级物理一致性、强距离单元 mask fallback 和检测后 refit 的总开关。
    // 默认关闭，优先保持实时性；专项效果评估时再显式开启。
    bool p38_enhanced_enable = false;
    bool p38_refit_enable = true; // 总开关开启后，是否执行 CFAR/聚类后 p38 二阶段重估
    int p38_refit_row_guard_bins = 2; // 检测点 row 方向排除半径
    int p38_refit_range_guard_bins = 2; // 检测点 range 方向排除半径
    double p38_refit_top_power_frac = 0.01; // 按 power_map 排除的顶部百分位
    int p38_refit_min_sample_count = 8; // p38_refit 最小样本行数
    double p38_refit_min_inlier_ratio = 0.60; // p38_refit 最小内点占比
    double p38_refit_max_rmse_rad = 0.60; // p38_refit 最大 RMSE
    double p38_refit_max_delta_k = 0.01; // p38_refit 与 p38_pre 的 k 限幅
    double p38_refit_max_delta_b_rad = 1.50; // p38_refit 与 p38_pre 的 b 限幅
    double p38_min_peak_row_energy_fraction = 0.05; // 以行能量稳健90%分位为参考的最低占比
    bool p38_theory_guided_fallback = true; // 总开关开启后，启用理论引导的环形拟合
    double p38_theory_prior_relative_span = 0.50; // 理论斜率附近搜索半宽比例
    double p38_theory_prior_trigger_relative_error = 0.35; // 触发引导拟合的相对偏差
    bool p38_diagnostics_dump = false; // 是否落盘逐波位 P38 JSON/样本 CSV；生产默认关闭
    double p38_expected_slope_rad_per_hz =
        std::numeric_limits<double>::quiet_NaN(); // 单波位运行时由基线/平台速度派生
    // 仅配对理论验证：复用 C+N 审计出的最终 CSI P38 参数，使三个 variant
    // 的 CSI 对消算子相同。两值均为 NaN 时保持生产的自适应 P38 拟合。
    double p38_csi_override_k_rad_per_hz =
        std::numeric_limits<double>::quiet_NaN();
    double p38_csi_override_b_rad =
        std::numeric_limits<double>::quiet_NaN();
    int ati_velocity_sign = 1; // ATI 相位到径向速度符号，必要时可设为 -1
    int ati_phase_to_velocity_sign = 1; // ATI 相位残差到径向速度的符号
    int motion_doppler_axis_sign = -1; // 径向速度到多普勒轴方向的符号
    std::string two_channel_phase_model = "ctdr"; // ctdr / legacy_equiv_dt
    int rx_baseline_sign = 1; // 共发双收 Rx1/Rx2 沿航迹顺序符号
    int channel_phase_sign = -1; // angle(F1*conj(F2)) 对接收光程差的相位符号
    double ati_phase_bias_rad = 0.0; // ATI 固定相位偏置
    // P8 静态杂波通道校准：用 CTDR 正演相位剥离物理光程差后，估计剩余
    // 相对复增益。只在 CTDR 定位边界扣除固定相位残差，不改写原始 P38/CSI。
    bool channel_calibration_enable = false;
    bool channel_calibration_apply_to_localization = true;
    // 绝对固定相位与系统/正演模型的常量残差单观测不可分。只有提供离线零扰动
    // 标定参考后才应用在线估计与参考之差；未提供时只输出诊断，绝不擅自改定位。
    bool channel_calibration_reference_valid = false;
    double channel_calibration_reference_phase_rad = 0.0;
    int channel_calibration_range_stride = 4;
    int channel_calibration_min_sample_count = 256;
    double channel_calibration_min_coherence = 0.30;
    double channel_calibration_outlier_threshold_rad = 0.50;
    double channel_calibration_max_rmse_rad = 0.35;
    // 标量复增益不能修复通道距离错位。负值保持旧行为（禁用剖面位移门）；
    // 非负值时只有双通道幅度剖面的估计相对位移不超过该值才允许应用。
    double channel_calibration_max_range_shift_bins = -1.0;
    double channel_calibration_min_range_correlation = 0.30;
    // 仅供单周期速度诊断/候选搜索使用，不得作为定位有效性门限。
    // 最终目标速度由 TrackManager 根据多周期 EN 位移/实际时间计算。
    double ati_vmax_mps = 50.0;
    double motion_comp_denom_min = 1.0e-6; // 解析式分母过小则回退旧定位
    double motion_comp_root_grid_step_mps = 0.02; // root1d 粗搜索步长
    double motion_comp_root_cost_max = 0.25; // root1d 最大可接受 cost
    bool motion_comp_debug = false; // 输出运动多普勒补偿调试字段/日志

    // P6 速度/相位联合模糊候选。默认关闭以保持旧 XML 的处理结果不变。
    // 单观测存在等价候选时只标记 unresolved，不能用最小速度分支冒充已解模糊。
    bool velocity_ambiguity_enable = false;
    double velocity_search_min_mps = -50.0;
    double velocity_search_max_mps = 50.0;
    int velocity_max_doppler_order = 8;
    int velocity_max_phase_order = 32;
    int velocity_max_candidates = 512;
    double velocity_beam_gate_deg = -1.0; // <=0 使用 loc_beam_gate_deg/boshu 派生值
    double velocity_phase_sigma_rad = 0.15;
    double velocity_beam_sigma_deg = 0.5;
    double velocity_cost_margin_min = 1.0;
    double velocity_equivalent_cost_tolerance = 1.0e-6;
    double velocity_speed_prior_mps = std::numeric_limits<double>::quiet_NaN();
    double velocity_speed_prior_sigma_mps = 0.0; // <=0 表示不使用速度先验

    // P5 CSI 指标 tap。默认关闭，避免生产模式发生额外 D2H 和矩阵写盘。
    // 开启后只读取生产 CSI/CFAR 实际使用的中间矩阵，不改变检测计算。
    bool csi_bypass_enable = false; // P5 零对消 sanity：输出幅度均衡后的通道1；默认关闭
    bool csi_metrics_enable = false;
    bool csi_metrics_dump_power_maps = false;
    // 功率图验收只需要 after_power 和坐标轴；默认仍保留完整诊断图，
    // 关闭后仅写 after_power/fa_axis/range_axis，避免实时测试落盘无关中间图。
    bool csi_metrics_dump_intermediate_maps = true;
    int csi_metrics_beam_id = -1; // <0 表示全部处理波位
    // legacy_min_magnitude: 旧 P38 线性相位 + 逐像素最小幅度均衡；
    // row_complex_ls: 每个多普勒行用距离训练单元估计复最小二乘系数。
    // row_phase_ls_linear: 每个多普勒行只使用复最小二乘系数的相位，
    //                      对所有距离单元执行线性复数相减。
    // legacy_min_magnitude: global P38 phase line + per-cell min magnitude.
    // row_phase_ls_min_magnitude: data-driven per-Doppler-row phase + the
    // same per-cell min-magnitude normalization (no channel amplification).
    std::string csi_cancellation_mode = "legacy_min_magnitude";
    // 当某一多普勒行没有足够的跨通道相干分量时，不执行非线性的逐像素
    // 最小幅度均衡/相减，直接保留通道1。这样无杂波行仍保持高斯噪声统计，
    // 同时全局 CSI 仍对相干杂波行生效。默认开启；显式 false 可复现 legacy path。
    bool csi_row_coherence_gate_enable = true;
    double csi_row_coherence_min = 0.50;
    // 部分对消增益。用于 row_phase_ls_linear 和
    // row_phase_ls_min_magnitude，范围 [0,1]；1 表示全量相位对消。
    double csi_subtraction_gain = 1.0;
    // CSI 第二遍处理是否应用逐距离相位校正。默认保持现有生产行为；关闭仅供
    // 诊断距离相位拟合是否破坏通道相干性，不影响定位使用的第一遍校正。
    bool csi_range_phase_correction_enable = true;
    // 仅供配对理论验证：将 C+N 审计出的两段距离相位校正冻结到 S-only 与
    // S+C+N。两个路径必须同时指定；默认空字符串时仍执行现有生产自适应拟合。
    std::string paired_raw_range_phase_override_f32;
    std::string paired_csi_range_phase_override_f32;
    int metrics_target_half_range_bins = 2;
    int metrics_target_half_doppler_bins = 2;
    int metrics_guard_range_bins = 4;
    int metrics_guard_doppler_bins = 4;
    int metrics_background_range_bins = 20;
    int metrics_background_doppler_bins = 16;
    double metrics_strong_peak_threshold_db = 15.0;
    int metrics_min_valid_background_cells = 100;

    // 生产 CFAR。旧实现把 70..190 行写死为禁检区；该范围只适用于旧矩阵尺寸，
    // 130 PRT 时会误删一半有效 Doppler 行。默认不设禁检区，若硬件实测需要可配置。
    int cfar_guard_cells = 4;
    int cfar_background_cells = 16;
    std::string cfar_type = "GO"; // CA / GO
    // 动态/增强 CFAR 总开关。关闭时固定使用与 v21 一致的单次杂波带 CFAR，
    // 禁用环形 Doppler、union/full 双定义和强小簇增强，并避免回传功率图。
    bool dynamic_cfar_enable = false;
    // Slow-time FFT rows form a periodic Doppler axis.  When enabled, CFAR
    // background/guard windows and cluster connectivity wrap across row
    // 0/Na-1 instead of discarding R=g+b rows at both Doppler edges.
    bool cfar_doppler_circular = true;
    // Dense targets can split a real peak into fewer CFAR-connected cells
    // because neighbouring targets contaminate each other's training rings.
    // The optional fallback admits a small component only when its peak is
    // far above the robust global power median; ordinary components still use
    // min_points.  This is disabled by default until a scenario opts in.
    bool cluster_strong_small_enable = false;
    int cluster_strong_small_min_points = 3;
    double cluster_strong_small_peak_over_median_db = 25.0;
    double cluster_max_phase_std_rad = 0.2;
    int cluster_max_range_gap = 2;
    // dynamic: only the static-clutter Doppler support uses CSI and rows
    // outside it use channel 2 (legacy behavior).
    // full: apply the already-computed CSI result to every Doppler row before
    // CFAR, which is required when targets may lie outside the clutter band.
    // split: run CSI/in-band CFAR and original-channel/out-of-band CFAR as two
    // independent detections, cluster each independently, then merge targets.
    // union: run both definitions and retain the per-cell union of their CFAR
    // hits.  This keeps legacy out-of-band sensitivity while still observing
    // low-speed targets whose Doppler falls outside the estimated clutter band.
    std::string csi_detection_band_mode = "dynamic"; // dynamic / full / split / union
    // Split-mode overlap protects the clutter-band boundary.  CSI CUTs extend
    // outwards and original-channel CUTs extend inwards by this many rows.
    int csi_split_boundary_guard_rows = 4;
    int csi_split_merge_doppler_bins = 2;
    int csi_split_merge_range_bins = 2;
    // Split-mode in-band CFAR can use an independent estimator because the
    // CSI clutter-band residual is not statistically identical to the
    // original-channel out-of-band data.  The default preserves the legacy
    // global CFAR type; production A/B runs may set CA here while keeping the
    // out-of-band branch on cfar_type (normally GO).
    std::string csi_split_in_band_cfar_type = "GO"; // CA / GO
    // Optional compact-component recovery for a real target whose CFAR
    // support is smaller than min_points.  It is disabled by default and
    // requires a peak sufficiently above the positive-power median.
    bool csi_split_small_cluster_enable = false;
    int csi_split_small_cluster_min_points = 3;
    // Local (non-CUT) background contrast required for an otherwise small
    // split-CFAR component.  The background is measured in a compact ring
    // around that component, not from the zero-padded branch map.
    double csi_split_small_cluster_peak_over_median_db = 20.0;
    int csi_split_small_cluster_near_doppler_rows = 4;
    int csi_split_small_cluster_near_range_bins = 3;
    // Optional rejection of residual single-range-bin Doppler columns.  Such
    // columns are characteristic of the observed low-power false alarms.
    bool csi_split_vertical_line_filter_enable = false;
    int csi_split_vertical_line_min_doppler_rows = 10;
    int csi_split_vertical_line_max_range_bins = 1;
    double csi_split_vertical_line_peak_over_median_db = 25.0;
    // The out-of-band split branch uses the original channel rather than the
    // CSI phase-corrected map.  Its connected components therefore must not be
    // rejected by a phase-consistency gate computed from the CSI snapshot.
    // Keep the gate configurable for A/B studies, but disable it by default.
    bool csi_split_out_of_band_phase_filter_enable = false;
    double csi_split_out_of_band_phase_max_std_rad = 0.2;
    // Common-transmit/dual-receive data are acquired simultaneously.  The
    // production default therefore keeps samples from both receive channels
    // together and lets measured/theoretical P38 remove the CTDR phase.  The
    // historical integer_equivalent_delay mode remains an explicit A/B option
    // only; it models the obsolete equivalent-time ATI interpretation.
    std::string csi_channel_alignment_mode = "none";
    int cfar_exclude_row_start = -1;
    int cfar_exclude_row_end = -1;

    // Doppler 中心使用逐距离门相关的稳健截尾复数和，防止强动目标
    // 或强散射点拉偏整条方位频率轴。
    bool doppler_center_robust_enable = true;
    double doppler_center_trim_top_fraction = 0.02;
    int doppler_center_min_valid_range_bins = 128;
    // 数据估计若偏离当前命令波位的静态几何中心超过一个方位波束支撑，
    // 说明被强动目标主导；此时回退 CTDR/平台参数正演中心。<=0 时按波束宽度派生门限。
    bool doppler_center_theory_guard_enable = true;
    double doppler_center_theory_max_error_hz = -1.0;
    // 配对仿真/回放可以固定为已审计的 wrapped Doppler 中心，使 C+N、S-only
    // 和 S+C+N 经过完全相同的动态支撑与 P38/CSI 算子。NaN（默认）保持生产的
    // 数据估计和理论保护路径不变。
    double doppler_center_override_hz =
        std::numeric_limits<double>::quiet_NaN();

    // 推导参数
    double lambda;              // 波长
    double R_bin;               // 距离采样分辨率
    std::vector<double> Rg;     // 距离向量
    double fd_res;              // 多普勒分辨率
    int rg_st, rg_ed, az_st, az_ed, az_center;  // 距离和方位支撑域
    int pkg_bytes;              // 每个脉冲数据包大小（字节）

    // ★ 新增：按 ROI 还是按 st..ed
    bool wavepos_use_roi = false;

    // ★ 新增：是否启用波位并行处理（true 表示并行，false 表示逐波位顺序处理）
    bool wavepos_parallel = true;
    int wavepos_parallel_max_workers = 4;
    bool enable_dbs_fusion = true;
    bool dbs_mosaic_use_gpu = true; // 仅控制最终展示拼图；信号处理 CUDA 路径不受影响
    double fusion_min_valid_beam_ratio = 1.0; // 全扫描中成功波位比例下限；1.0=严格全成功
    double dbs_out_res_m = 25.0; // DBS 成像输出分辨率，对应 XML raw_fenbianlv
    int dbs_beam_skip = 1;      // DBS 波位跳过数，对应 XML n_tiaoguo
    int dbs_range_skip = 1;     // DBS 距离抽样步长，对应 XML len_tiaoguo
    int dbs_interp_mode = 1;    // DBS 拼图插值模式，1=最近邻，2=双线性
    std::string dbs_mosaic_mode = "max"; // max(default) / weighted
    size_t dbs_max_mosaic_pixels = 200000000ULL; // DBS 拼图像素数上限，对应 XML dbs_max_mosaic_pixels
    double dbs_mosaic_margin_ratio = 0.05; // DBS 拼图范围比例裕量，对应 XML dbs_mosaic_margin_ratio
    double dbs_mosaic_margin_m = 200.0;    // DBS 拼图范围最小裕量（米），对应 XML dbs_mosaic_margin_m

    // ★ 新增：是否估计误差角（false 时直接使用 XML 中的 squint_angle）
    bool estimate_error_angle = true;

    // ★ ROI 四角经纬度（度）：[lat1,lng1, lat2,lng2, lat3,lng3, lat4,lng4]
    std::array<double,4> roi_ll_deg;  // 调用前请填好

    // ★ 斜视角有效扫描范围（度），默认 [-25, 25]；离散波位由 wavepos_st/ed/skip 约束
    double scan_min_deg = -25.0;
    double scan_max_deg =  25.0;
    double lat_st;              
    double lat_ed;
    double lon_st;
    double lon_ed;

    int squint_side = 0;      // 右斜视侧，默认0
    double squint_angle = 0.0; // 斜视角度，单位：度
};

inline int effectivePulseNum(const Config& cfg)
{
    if (cfg.process_pulse_num > 0) {
        return cfg.process_pulse_num;
    }
    if (cfg.INFO_Type && cfg.read_pulse_num > 0) {
        return cfg.read_pulse_num;
    }
    return cfg.pulse_num;
}

inline int effectiveRangeFftLen(const Config& cfg)
{
    return cfg.range_fft_len > 0 ? cfg.range_fft_len : cfg.pulse_len;
}

inline int effectiveRangeCompressLen(const Config& cfg)
{
    return cfg.range_compress_len > 0 ? cfg.range_compress_len : cfg.rg_len;
}

inline bool usesRangeCropWindow(const Config& cfg)
{
    return effectiveRangeFftLen(cfg) != cfg.pulse_len ||
           cfg.range_crop_start != 0;
}

// 定义结构体用于存储输出数据
struct GMTIOutput {
    // 原始数据
    std::vector<std::complex<double>> orgdata;

    // 时间戳
    double utcMid;

    // 飞机位置
    struct Plane {
        double V;    // 速度
        double E;    // 东向位置
        double N;    // 北向位置
        double H;    // 高度
        double V_angle;  // 飞机航向角
    } plane;

    // 数据处理结果
    struct Detect {
        std::vector<int> prow;
        std::vector<int> pcol;
        std::vector<double> row_af;
        std::vector<std::vector<double>> MTpos;
    } detect;

    // 多普勒频率
    std::vector<double> fa_c;

    // 相位
    std::vector<double> phi_diss;
    std::vector<double> phi_fit;

    // CSI 结果
    std::vector<std::complex<double>> GMTIclean;
    std::vector<std::complex<double>> echoF1;

    //
    std::vector<double> MT;

    struct DetectionCsvRecord {
        int period_id = -1;
        int beam_id = -1;
        ScanMode scan_mode = ScanMode::Electronic;
        int scan_id = -1;
        int window_id = -1;
        std::size_t pulse_start = 0;
        std::size_t pulse_end = 0;
        double cpi_center_utc = std::numeric_limits<double>::quiet_NaN();
        double az_start_deg = std::numeric_limits<double>::quiet_NaN();
        double az_center_deg = std::numeric_limits<double>::quiet_NaN();
        double az_end_deg = std::numeric_limits<double>::quiet_NaN();
        double az_span_deg = std::numeric_limits<double>::quiet_NaN();
        int scan_direction = 0;
        double platform_e = std::numeric_limits<double>::quiet_NaN();
        double platform_n = std::numeric_limits<double>::quiet_NaN();
        double platform_h = std::numeric_limits<double>::quiet_NaN();
        double platform_v = std::numeric_limits<double>::quiet_NaN();
        double platform_v_angle_deg = std::numeric_limits<double>::quiet_NaN();
        double fd_ctr_wrapped = std::numeric_limits<double>::quiet_NaN();
        double fd_ctr_unwrapped = std::numeric_limits<double>::quiet_NaN();
        int range_bin = -1;
        int row = -1;
        int col = -1;
        double range_m = std::numeric_limits<double>::quiet_NaN();
        double theta_cmd_deg = std::numeric_limits<double>::quiet_NaN();
        double theta_true_deg = std::numeric_limits<double>::quiet_NaN();
        double e = std::numeric_limits<double>::quiet_NaN();
        double n = std::numeric_limits<double>::quiet_NaN();
        double lat = std::numeric_limits<double>::quiet_NaN();
        double lon = std::numeric_limits<double>::quiet_NaN();
        double utc = std::numeric_limits<double>::quiet_NaN();
        double amplitude = std::numeric_limits<double>::quiet_NaN();
        double radial_velocity_mps = std::numeric_limits<double>::quiet_NaN();
        double phase_rad = std::numeric_limits<double>::quiet_NaN();
        double range_phase_correction_rad = std::numeric_limits<double>::quiet_NaN();
        double ctdr_raw_phase_rad = std::numeric_limits<double>::quiet_NaN();
        double p38_k = std::numeric_limits<double>::quiet_NaN();
        double p38_b = std::numeric_limits<double>::quiet_NaN();
        double phi_static_model_rad = std::numeric_limits<double>::quiet_NaN();
        std::string phi_static_model_name;
        double C_ati = std::numeric_limits<double>::quiet_NaN();
        double k_eff_static_phase_df = std::numeric_limits<double>::quiet_NaN();
        double phi_static_rad = std::numeric_limits<double>::quiet_NaN();
        double phi_static_total_rad = std::numeric_limits<double>::quiet_NaN();
        double phi_res_rad = std::numeric_limits<double>::quiet_NaN();
        double phi_static_at_zero = std::numeric_limits<double>::quiet_NaN();
        double phi_res_at_zero = std::numeric_limits<double>::quiet_NaN();
        double phi_static_geometry_rad = std::numeric_limits<double>::quiet_NaN();
        double af_phase = std::numeric_limits<double>::quiet_NaN();
        double af_total = std::numeric_limits<double>::quiet_NaN();
        double af_geometry = std::numeric_limits<double>::quiet_NaN();
        double af_motion = std::numeric_limits<double>::quiet_NaN();
        double phi_motion = std::numeric_limits<double>::quiet_NaN();
        double delta_t_s = std::numeric_limits<double>::quiet_NaN();
        double motion_comp_denom = std::numeric_limits<double>::quiet_NaN();
        double denom_without_k = std::numeric_limits<double>::quiet_NaN();
        double v_from_phase_raw = std::numeric_limits<double>::quiet_NaN();
        double v_from_phi_res = std::numeric_limits<double>::quiet_NaN();
        double v_iterative_mps = std::numeric_limits<double>::quiet_NaN();
        double v_analytic_mps = std::numeric_limits<double>::quiet_NaN();
        double v_root1d_mps = std::numeric_limits<double>::quiet_NaN();
        double v_old_mps = std::numeric_limits<double>::quiet_NaN();
        double af_geometry_old_hz = std::numeric_limits<double>::quiet_NaN();
        double af_geometry_iterative_hz = std::numeric_limits<double>::quiet_NaN();
        double af_geometry_analytic_hz = std::numeric_limits<double>::quiet_NaN();
        double af_geometry_root1d_hz = std::numeric_limits<double>::quiet_NaN();
        double root1d_cost = std::numeric_limits<double>::quiet_NaN();
        double af_alias_hz = std::numeric_limits<double>::quiet_NaN();
        double af_unwrapped_hz = std::numeric_limits<double>::quiet_NaN();
        int doppler_ambiguity_order = 0;
        double phase_wrapped_rad = std::numeric_limits<double>::quiet_NaN();
        double phase_unwrapped_rad = std::numeric_limits<double>::quiet_NaN();
        int phase_ambiguity_order = 0;
        int velocity_candidate_count = 0;
        double velocity_best_cost = std::numeric_limits<double>::quiet_NaN();
        double velocity_second_cost = std::numeric_limits<double>::quiet_NaN();
        double velocity_cost_margin = std::numeric_limits<double>::quiet_NaN();
        std::string velocity_solver;
        std::string velocity_confidence;
        std::string ambiguity_status;
        double p38_raw_k = std::numeric_limits<double>::quiet_NaN();
        double p38_raw_b = std::numeric_limits<double>::quiet_NaN();
        double p38_raw_rmse = std::numeric_limits<double>::quiet_NaN();
        double p38_raw_inlier_ratio = std::numeric_limits<double>::quiet_NaN();
        double p38_pre_k = std::numeric_limits<double>::quiet_NaN();
        double p38_pre_b = std::numeric_limits<double>::quiet_NaN();
        double p38_pre_rmse = std::numeric_limits<double>::quiet_NaN();
        double p38_refit_k = std::numeric_limits<double>::quiet_NaN();
        double p38_refit_b = std::numeric_limits<double>::quiet_NaN();
        double p38_refit_rmse = std::numeric_limits<double>::quiet_NaN();
        int p38_refit_sample_count = 0;
        double p38_refit_inlier_ratio = std::numeric_limits<double>::quiet_NaN();
        int p38_refit_valid = 0;
        // Per-candidate production CFAR peak margin, in dB.  It is populated
        // when the diagnostics path downloads the power/threshold maps.
        double cfar_margin_db = std::numeric_limits<double>::quiet_NaN();
        double p38_used_k = std::numeric_limits<double>::quiet_NaN();
        double p38_used_b = std::numeric_limits<double>::quiet_NaN();
        std::string p38_used_source;
        double phi_static_pre_rad = std::numeric_limits<double>::quiet_NaN();
        double phi_res_pre_rad = std::numeric_limits<double>::quiet_NaN();
        double v_pre_mps = std::numeric_limits<double>::quiet_NaN();
        double phi_static_refit_rad = std::numeric_limits<double>::quiet_NaN();
        double phi_res_refit_rad = std::numeric_limits<double>::quiet_NaN();
        double v_refit_mps = std::numeric_limits<double>::quiet_NaN();
        double sinA_old = std::numeric_limits<double>::quiet_NaN();
        double sinA_comp = std::numeric_limits<double>::quiet_NaN();
        double sinA_used = std::numeric_limits<double>::quiet_NaN();
        double angle_from_sinA_deg = std::numeric_limits<double>::quiet_NaN();
        double theta_used_for_position_deg = std::numeric_limits<double>::quiet_NaN();
        double look_from_sinA_e = std::numeric_limits<double>::quiet_NaN();
        double look_from_sinA_n = std::numeric_limits<double>::quiet_NaN();
        double look_e_diff = std::numeric_limits<double>::quiet_NaN();
        double look_n_diff = std::numeric_limits<double>::quiet_NaN();
        double old_e = std::numeric_limits<double>::quiet_NaN();
        double old_n = std::numeric_limits<double>::quiet_NaN();
        double new_e = std::numeric_limits<double>::quiet_NaN();
        double new_n = std::numeric_limits<double>::quiet_NaN();
        int old_valid = 0;
        int comp_valid = 0;
        int old_invalid_comp_valid = 0;
        int motion_comp_valid = 0;
        int motion_comp_enable = 0;
        int motion_comp_used = 0;
        int motion_comp_fallback = 0;
        int p38_theory_sign = 0;
        int motion_doppler_axis_sign = 0;
        int ati_phase_to_velocity_sign = 0;
        std::string p38_mode;
        std::string geometry_calib_mode;
        std::string loc_used_mode;
        std::string motion_comp_status;
        std::string motion_comp_solver;
    };
    std::vector<DetectionCsvRecord> detection_records;
};

// 建议放到 config_structs.hpp 或 GMTIOutput 定义处
struct TargetSelection {
    std::vector<int> prow;                       // 行索引
    std::vector<int> pcol;                       // 列索引
    std::vector<double> A;                       // 幅度
    std::vector<std::complex<double>> ch1_Data;  // 通道1复数
    std::vector<std::complex<double>> ch2_Data;  // 通道2复数
};

struct Track {
    std::array<double,4> x{};
    std::array<double,16> P{};
    std::vector<std::array<double,2>> pos;
    std::vector<std::array<double,2>> kf;
    std::vector<std::array<double,4>> x_state;
    std::vector<double> time;
    double direction = 0.0;
    double range = 0.0;
    int last = 0;
    int missed = 0;
    int id = 0;
};

#endif // CONFIG_STRUCTS_HPP
