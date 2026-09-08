#include "dbs/DbsStitcher.hpp"
#include "dbs/DbsIO.hpp"
#include <fstream>
#include <numeric>
#include <cmath>
#include <stdexcept>
#include "dbs/lodepng.h" // PNG写出
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <algorithm>
#include <vector>
#include <cstdint>
#include <iostream>
#include <limits>
#include <cerrno>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h> // opendir, readdir, closedir
#include "dbs/rotation_xy.hpp"
#include "trig_lut.hpp"

static inline float c0() { return 299792458.0f; }

static inline float clamp_unit_float(float x)
{
  return x < -1.0f ? -1.0f : (x > 1.0f ? 1.0f : x);
}

static inline float wrap180_float(float angle_deg)
{
  angle_deg = std::fmod(angle_deg + 180.0f, 360.0f);
  if (angle_deg < 0.0f)
    angle_deg += 360.0f;
  return angle_deg - 180.0f;
}

static inline bool in_beam_angle_gate(float fd, float lambda, float velocity,
                                      float beam_angle_deg, float gate_deg)
{
  if (!(velocity > 0.0f) || !(lambda > 0.0f) || !(gate_deg > 0.0f))
    return true;
  const float ratio = clamp_unit_float(-fd * lambda / (2.0f * velocity));
  const float pixel_angle_deg =
      gmti::trig_lut::asin(ratio) * 180.0f / static_cast<float>(M_PI);
  return std::fabs(wrap180_float(pixel_angle_deg - beam_angle_deg)) <= gate_deg;
}

DbsStitcher::DbsStitcher()
{
}
DbsStitcher::~DbsStitcher()
{
}

// ---------- 小工具：递归创建目录 ----------
bool DbsStitcher::mkdir_p(const std::string &dir)
{
  if (dir.empty())
    return true;
  std::string cur;
  size_t i = 0;
  if (dir[0] == '/')
  {
    cur = "/";
    i = 1;
  }
  for (; i < dir.size(); ++i)
  {
    cur.push_back(dir[i]);
    if (dir[i] == '/' || i == dir.size() - 1)
    {
      if (cur == "/" || cur == "./" || cur == ".")
        continue;
      if (::mkdir(cur.c_str(), 0755) != 0)
      {
        if (errno == EEXIST)
          continue;
        if (errno == ENOENT)
          continue; // 父目录尚未就绪，继续循环
        std::cerr << "mkdir_p failed: " << cur << " errno=" << errno << "\n";
        return false;
      }
    }
  }
  return true;
}

static inline bool starts_with(const char *s, const char *pfx)
{
  while (*pfx)
  {
    if (*s++ != *pfx++)
      return false;
  }
  return true;
}
static inline bool ends_with(const char *s, const char *sfx)
{
  const size_t ls = std::strlen(s), lr = std::strlen(sfx);
  return (ls >= lr) && (std::strcmp(s + (ls - lr), sfx) == 0);
}

static std::string gmti_product_name(int index, const std::string &suffix)
{
  std::string number = std::to_string(index);
  if (number.size() < 2U) {
    number.insert(0, 2U - number.size(), '0');
  }
  return std::string("GMTI") + number + suffix;
}

static bool parse_gmti_index(const char *name, const char *suffix, int &index)
{
  index = 0;
  const size_t name_len = std::strlen(name);
  const size_t suffix_len = std::strlen(suffix);
  if (name_len <= 4U + suffix_len || !starts_with(name, "GMTI") ||
      !ends_with(name, suffix)) {
    return false;
  }

  const size_t digits_end = name_len - suffix_len;
  int value = 0;
  for (size_t pos = 4U; pos < digits_end; ++pos) {
    if (name[pos] < '0' || name[pos] > '9') {
      return false;
    }
    const int digit = name[pos] - '0';
    if (value > (std::numeric_limits<int>::max() - digit) / 10) {
      return false;
    }
    value = value * 10 + digit;
  }
  if (value <= 0) {
    return false;
  }
  index = value;
  return true;
}

bool DbsStitcher::nextGMTIFileNamePNG(const std::string &dir,
                                      std::string &out_path,
                                      int &out_index) const
{
  out_path.clear();
  out_index = 0;

  // 1) 确保目录存在
  std::string d = dir;
  if (!d.empty() && d.back() != '/' && d.back() != '\\')
    d.push_back('/');
  if (!mkdir_p(d))
  {
    std::cerr << "nextGMTIFileName: 创建目录失败: " << d << "\n";
    return false;
  }

  // 2) 打开目录并扫描现有文件，匹配 "GMTIxx.bin"
  int max_idx = 0;

  DIR *dp = ::opendir(d.c_str());
  if (dp)
  {
    while (dirent *ent = ::readdir(dp))
    {
      const char *name = ent->d_name;
      // 过滤掉 "." ".."
      if (name[0] == '.')
        continue;

      // 格式：GMTI + 一个或多个十进制编号 + .png。
      int idx = 0;
      if (parse_gmti_index(name, ".png", idx) && idx > max_idx) {
        max_idx = idx;
      }
    }
    ::closedir(dp);
  }
  else
  {
    // 目录不可读也不致命（可能刚创建），从 01 开始
  }

  // 3) 下一个序号
  if (max_idx == std::numeric_limits<int>::max()) {
    std::cerr << "nextGMTIFileName: 编号超过 int 可表示范围，无法继续编号。\n";
    return false;
  }
  const int next_idx = max_idx + 1;

  // 4) 组装路径
  out_path = d + gmti_product_name(next_idx, ".png");
  out_index = next_idx;
  return true;
}

bool DbsStitcher::nextGMTIFileNameTxt(const std::string &dir,
                                      std::string &out_path,
                                      int &out_index) const
{
  out_path.clear();
  out_index = 0;

  // 1) 确保目录存在
  std::string d = dir;
  if (!d.empty() && d.back() != '/' && d.back() != '\\')
    d.push_back('/');
  if (!mkdir_p(d))
  {
    std::cerr << "nextGMTIFileName: 创建目录失败: " << d << "\n";
    return false;
  }

  // 2) 打开目录并扫描现有文件，匹配 "GMTIxx.bin"
  int max_idx = 0;

  DIR *dp = ::opendir(d.c_str());
  if (dp)
  {
    while (dirent *ent = ::readdir(dp))
    {
      const char *name = ent->d_name;
      // 过滤掉 "." ".."
      if (name[0] == '.')
        continue;

      // 格式：GMTI + 一个或多个十进制编号 + .txt。
      int idx = 0;
      if (parse_gmti_index(name, ".txt", idx) && idx > max_idx) {
        max_idx = idx;
      }
    }
    ::closedir(dp);
  }
  else
  {
    // 目录不可读也不致命（可能刚创建），从 01 开始
  }

  // 3) 下一个序号
  if (max_idx == std::numeric_limits<int>::max()) {
    std::cerr << "nextGMTIFileName: 编号超过 int 可表示范围，无法继续编号。\n";
    return false;
  }
  const int next_idx = max_idx + 1;

  // 4) 组装路径
  out_path = d + gmti_product_name(next_idx, ".txt");
  out_index = next_idx;
  return true;
}

bool DbsStitcher::loadPosAndEstimateVelocity(const Params &P, PosData &POS)
{
  // 读取 POS: 每帧 7 个 double
  std::ifstream ifs(P.pos_path, std::ios::binary);
  if (!ifs)
    return false;
  const size_t elems = 7;
  std::vector<double> buf(elems);
  while (ifs.read(reinterpret_cast<char *>(buf.data()), elems * sizeof(double)))
  {
    POS.gpstime.push_back(buf[0]); // 时间戳
    POS.lat_rad.push_back(buf[1]); // 纬度 (弧度)
    POS.lon_rad.push_back(buf[2]); // 经度 (弧度)
    POS.alt_m.push_back(buf[3]);   // 高度 (米)
  }
  size_t N = POS.gpstime.size();
  POS.vx.resize(N);
  POS.vy.resize(N);
  POS.vz.resize(N);
  POS.speed.resize(N);
  POS.x_m.resize(N);
  POS.y_m.resize(N);

  for (size_t i = 0; i < N; i++)
  {
    double lat_deg = POS.lat_rad[i] * 180.0 / M_PI;
    double lon_deg = POS.lon_rad[i] * 180.0 / M_PI;
    proj_ll_to_xy(lat_deg, lon_deg, POS.x_m[i], POS.y_m[i]);
  }
  // 中心差分速度
  auto diff = [&](const std::vector<double> &v, std::vector<double> &dv)
  {
    size_t N = v.size();
    dv.resize(N);
    for (size_t i = 0; i < N; i++)
    {
      size_t i0 = (i > 4) ? i - 4 : i;
      size_t i1 = (i + 4 < N) ? i + 4 : i;
      double dt = POS.gpstime[i1] - POS.gpstime[i0];
      dv[i] = (dt != 0.0) ? (v[i1] - v[i0]) / dt : 0.0;
    }
  };
  diff(POS.x_m, POS.vx);
  diff(POS.y_m, POS.vy);
  diff(POS.alt_m, POS.vz);
  for (size_t i = 0; i < N; i++)
    POS.speed[i] = std::sqrt(POS.vx[i] * POS.vx[i] + POS.vy[i] * POS.vy[i] + POS.vz[i] * POS.vz[i]);

  return true;
}

  int DbsStitcher::estimateEffectiveAzBins(const Params &P, const PosData &POS)
  {
    // 与 MATLAB 逻辑一致的估算（略简化）
    double Rbin = c0() / (2.0 * P.fs_hz);
    double air_h = (std::accumulate(POS.alt_m.begin(), POS.alt_m.end(), 0.0) / POS.alt_m.size()) - P.mean_ground_h;
    double rmax = P.Rmin_m + P.range_samp_used * Rbin;
    double max_cosphi = std::sqrt(std::max(0.0, 1.0 - (air_h / rmax) * (air_h / rmax)));

    int azN = std::max(1, int(std::floor(P.scan_max_az_deg)));
    double max_delta_sin = 0;
    for (int i = 1; i <= azN; i++)
    {
      double d = std::abs(gmti::trig_lut::sin((i + (P.beamwidth_deg + 1) / 2) * M_PI / 180.0) - gmti::trig_lut::sin((i - (P.beamwidth_deg + 1) / 2) * M_PI / 180.0));
      max_delta_sin = std::max(max_delta_sin, d);
    }
    double lam = c0() / P.fc_hz;
    double v_med = 0.0;
    if (POS.speed.size() > 100) // 确保有足够的元素
    {
      // 从索引100到末尾
      auto max_iter = std::max_element(POS.speed.begin() + 100, POS.speed.end());
      if (max_iter != POS.speed.end())
      {
        v_med = *max_iter;
      }
    }

    double max_dopp = 2 * v_med / lam * max_delta_sin * max_cosphi;
    int nEff = int(std::ceil(max_dopp / (1.0 * P.PRF / P.pulses_per_beam)));
    nEff = int(std::floor(nEff * 1.4));
    if (nEff % 2)
      nEff++;

    return nEff;
  }

bool DbsStitcher::estimateMosaicExtent(const Config &cfg, const RDData &RD, const MetaPack &meta,
                                       Bounds &b, Grid &grid)
{
  // === 用你“精简版静止目标定位”逻辑：只更新 min/max ===
  const int B = (int)meta.beams.size(); // bowei_num
  const int M = cfg.rg_len;
  const int nEff = RD.nEff;
  if (B <= 0 || M <= 0 || nEff <= 0 || cfg.fc <= 0.0 || cfg.dbs_out_res_m <= 0.0 ||
      RD.fd_axis.size() < static_cast<size_t>(B) || RD.rg_axis.size() < static_cast<size_t>(B))
  {
    std::cerr << "[fusion][dbs][extent] invalid inputs: B=" << B
              << " M=" << M << " nEff=" << nEff
              << " fc=" << cfg.fc << " out_res=" << cfg.dbs_out_res_m << std::endl;
    return false;
  }

  float lam = float(c0() / cfg.fc);
  float minX = std::numeric_limits<float>::infinity();
  float maxX = -std::numeric_limits<float>::infinity();
  float minY = std::numeric_limits<float>::infinity();
  float maxY = -std::numeric_limits<float>::infinity();

  int step = std::max(1, cfg.dbs_range_skip);
  const float beam_angle_gate_deg = static_cast<float>(
      cfg.loc_beam_gate_deg > 0.0 ? cfg.loc_beam_gate_deg
                                  : std::max(0.0, cfg.beamwidth_deg * 0.5) + 0.5);

  std::vector<float> vN(B), vE(B), V_feiji(B), sin_jiaodu(B), cos_jiaodu(B);
  for (int b = 0; b < B; ++b)
  {
    vN[b] = meta.beams[b].vN;
    vE[b] = meta.beams[b].vE;
    V_feiji[b] = std::sqrt(vN[b] * vN[b] + vE[b] * vE[b]);
    const float denom = (std::fabs(vE[b]) > 1e-12f) ? std::fabs(vE[b]) : 1e-12f;
    const float jiaodu = gmti::trig_lut::atan(std::fabs(vN[b] / denom));
    sin_jiaodu[b] = gmti::trig_lut::sin(jiaodu);
    cos_jiaodu[b] = gmti::trig_lut::cos(jiaodu);
  }

  for (int n = 0; n < B; n++)
  {
    if ((n % std::max(1, cfg.dbs_beam_skip)) != 0)
      continue;

    const auto &fdv = RD.fd_axis[n]; // 1 x nEff
    const auto &rgv = RD.rg_axis[n]; // 1 x M
    if (fdv.size() < static_cast<size_t>(nEff) || rgv.size() < static_cast<size_t>(M))
    {
      std::cerr << "[fusion][dbs][extent] skip beam " << n
                << " due to axis size mismatch: fd=" << fdv.size()
                << " rg=" << rgv.size() << std::endl;
      continue;
    }

    float V_f = V_feiji[n];
    if (!std::isfinite(V_f) || V_f <= 1e-3f)
    {
      std::cerr << "[fusion][dbs][extent] skip beam " << n
                << " due to invalid speed V=" << V_f << std::endl;
      continue;
    }
    float Height = meta.beams[n].z - float(cfg.MT_nowz);
    float start_x = meta.beams[n].x, start_y = meta.beams[n].y;

    int flag = infer_flight_dir_flag(vN, vE); // 1/2/3/4
    int squint_side = cfg.squint_side;           // 0 for left, 1 for right
    flag = flag + squint_side * 4;


    for (int i = 0; i < M; i += step)
    {
      float R = rgv[i];
      for (int jj = 0; jj < nEff; jj++)
      {
        float fd = fdv[jj];
        if (!in_beam_angle_gate(fd, lam, V_f, meta.beams[n].angle_deg,
                                beam_angle_gate_deg))
          continue;
        float x0 = R * lam * fd / (2 * V_f);
        float t2 = R * R - x0 * x0 - Height * Height;
        if (t2 <= 0)
          continue;
        float y0 = std::sqrt(t2);

        float tx, ty;
        rotation_xy(x0, y0,
                    flag,
                    cos_jiaodu[n], sin_jiaodu[n],
                    tx, ty);

        float gx = tx + start_x;
        float gy = ty + start_y;
        if (!std::isfinite(gx) || !std::isfinite(gy))
          continue;
        minX = std::min(minX, gx);
        maxX = std::max(maxX, gx);
        minY = std::min(minY, gy);
        maxY = std::max(maxY, gy);
      }
    }
  }

  b.minX = minX;
  b.maxX = maxX;
  b.minY = minY;
  b.maxY = maxY;
  if (!std::isfinite(minX) || !std::isfinite(maxX) ||
      !std::isfinite(minY) || !std::isfinite(maxY) ||
      !(maxX > minX) || !(maxY > minY))
  {
    std::cerr << "[fusion][dbs][extent] invalid bounds: X=[" << minX << ", " << maxX
              << "] Y=[" << minY << ", " << maxY << "]" << std::endl;
    return false;
  }

  const double rawSpanX = static_cast<double>(maxX) - static_cast<double>(minX);
  const double rawSpanY = static_cast<double>(maxY) - static_cast<double>(minY);
  const double marginRatio = std::max(0.0, cfg.dbs_mosaic_margin_ratio);
  const double minMarginM = std::max(0.0, cfg.dbs_mosaic_margin_m);
  const double marginX = std::max(minMarginM, rawSpanX * marginRatio);
  const double marginY = std::max(minMarginM, rawSpanY * marginRatio);
  minX = static_cast<float>(static_cast<double>(minX) - marginX);
  maxX = static_cast<float>(static_cast<double>(maxX) + marginX);
  minY = static_cast<float>(static_cast<double>(minY) - marginY);
  maxY = static_cast<float>(static_cast<double>(maxY) + marginY);

  // 生成规则网格
  double dx = std::max(1.0, cfg.dbs_out_res_m);
  const double spanX = static_cast<double>(maxX) - static_cast<double>(minX);
  const double spanY = static_cast<double>(maxY) - static_cast<double>(minY);
  auto axis_count = [](double span, double res) -> double {
    return std::floor(span / res) + 1.0;
  };

  double nxD = axis_count(spanX, dx);
  double nyD = axis_count(spanY, dx);
  double pixD = nxD * nyD;
  const size_t maxPixels = std::max<size_t>(1, cfg.dbs_max_mosaic_pixels);
  if (!std::isfinite(nxD) || !std::isfinite(nyD) || !std::isfinite(pixD) ||
      nxD <= 0.0 || nyD <= 0.0)
  {
    std::cerr << "[fusion][dbs][extent] invalid grid estimate: spanX=" << spanX
              << " spanY=" << spanY << " dx=" << dx << std::endl;
    return false;
  }

  if (pixD > static_cast<double>(maxPixels))
  {
    const double oldDx = dx;
    const double oldPixD = pixD;
    dx *= std::sqrt(pixD / static_cast<double>(maxPixels));
    nxD = axis_count(spanX, dx);
    nyD = axis_count(spanY, dx);
    pixD = nxD * nyD;
    std::cerr << "[fusion][dbs][extent][warn] mosaic grid too large at requested resolution: "
              << "spanX=" << spanX << "m spanY=" << spanY << "m out_res=" << oldDx
              << "m pixels~" << static_cast<unsigned long long>(oldPixD)
              << ". Auto coarsen output resolution to " << dx
              << "m. Set XML <dbs_max_mosaic_pixels> to override the cap (current "
              << maxPixels << ")." << std::endl;
  }

  if (pixD > static_cast<double>(maxPixels) * 1.02)
  {
    std::cerr << "[fusion][dbs][extent] grid still exceeds cap after coarsening: pixels~"
              << pixD << " cap=" << maxPixels << std::endl;
    return false;
  }

  const size_t nx = static_cast<size_t>(nxD);
  const size_t ny = static_cast<size_t>(nyD);
  grid.x.assign(nx, 0.0f);
  grid.y.assign(ny, 0.0f);
  for (size_t i = 0; i < nx; ++i)
    grid.x[i] = static_cast<float>(static_cast<double>(minX) + static_cast<double>(i) * dx);
  for (size_t i = 0; i < ny; ++i)
    grid.y[i] = static_cast<float>(static_cast<double>(minY) + static_cast<double>(i) * dx);
  grid.dx = static_cast<float>(dx);
  grid.dy = static_cast<float>(dx);

  const double hostMosaicGiB = static_cast<double>(nx) * static_cast<double>(ny) *
                               static_cast<double>(sizeof(float) + sizeof(uint16_t)) /
                               (1024.0 * 1024.0 * 1024.0);
  if (cfg.runtime_diagnostics_enabled) {
    std::cout << "[fusion][dbs][extent] B=" << B << " M=" << M << " nEff=" << nEff
              << " boundsX=[" << minX << ", " << maxX << "] boundsY=[" << minY << ", " << maxY
              << "] marginX=" << marginX << "m marginY=" << marginY
              << "m grid=" << nx << "x" << ny << " dx=" << grid.dx
              << " host_mosaic_min~" << hostMosaicGiB << " GiB" << std::endl;
  }
  return true;
}

// ---- 小工具：转置、左右翻转、均值、PNG写出(8bit灰度) ----
template <typename T>
static Image2D<T> transpose_copy(const Image2D<T> &src)
{
  Image2D<T> dst(src.cols, src.rows);
  for (int r = 0; r < src.rows; ++r)
    for (int c = 0; c < src.cols; ++c)
      dst.at(c, r) = src.at(r, c);
  return dst;
}
template <typename T>
static void fliplr_inplace(Image2D<T> &img)
{
  for (int r = 0; r < img.rows; ++r)
  {
    for (int c = 0; c < img.cols / 2; ++c)
    {
      std::swap(img.at(r, c), img.at(r, img.cols - 1 - c));
    }
  }
}
static double mean_of_image(const Image2D<float> &img)
{
  if (img.empty())
    return 0.0;
  double s = 0.0;
  for (size_t i = 0; i < img.buf.size(); ++i)
    s += img.buf[i];
  return s / (double)img.buf.size();
}
// 使用 lodepng 写 8bit 灰度 PNG（data 按行主）
static bool write_png_gray8(const std::string &path,
                            const unsigned char *data,
                            unsigned width, unsigned height)
{
  unsigned err = lodepng_encode_file(path.c_str(), data, width, height, LCT_GREY, 8);
  return err == 0;
}

bool DbsStitcher::writeProducts(const Config &cfg,
                                const Grid &grid,
                                const Mosaic &mosaic,
                                const Bounds &b,
                                const MetaPack *meta)
{
  (void)grid;
  (void)meta;
  // 1) 阈值截断 + 归一化到 PNG 灰度。旧实现依次保留了一份 float、
  // uint16 和转置 uint16 图；全幅 DBS 图约数千万像素时，这些额外拷贝会
  // 放大每周期的内存带宽和分配抖动。这里按完全相同的量化/转置/翻转规则
  // 直接写入最终 8-bit 图。
  const double th_src = mean_of_image(mosaic.amp) * 13.0;
  const double th = (th_src > 1e-12) ? th_src : 1.0; // 防止除零

#ifdef DEBUG
  Image2D<float> temp_src = mosaic.r_save;
  Image2D<float> temp_dst = transpose_copy(temp_src);
  fliplr_inplace(temp_dst);

  std::ofstream src2dst_cfs0("DBS_range.dat", std::ios::binary);
  if (!src2dst_cfs0)
    {
        std::cerr << "writeResult: 无法打开输出文件: " << "DBS_range.dat" << "\n";
        return false;
    }
  src2dst_cfs0.write(reinterpret_cast<const char *>(temp_dst.buf.data()),
            static_cast<std::streamsize>(temp_dst.buf.size() * 4));

  temp_src = mosaic.fd_save;
  temp_dst = transpose_copy(temp_src);
  fliplr_inplace(temp_dst);

  std::ofstream src2dst_cfs1("DBS_doppler.dat", std::ios::binary);
  if (!src2dst_cfs1)
    {
        std::cerr << "writeResult: 无法打开输出文件: " << "DBS_doppler.dat" << "\n";
        return false;
    }
  src2dst_cfs1.write(reinterpret_cast<const char *>(temp_dst.buf.data()),
            static_cast<std::streamsize>(temp_dst.buf.size() * 4));

#endif

  // 2) 写 PNG（8bit）：逻辑上先转置再左右翻转，与原 MATLAB 输出一致。
  const int output_rows = mosaic.amp.cols;
  const int output_cols = mosaic.amp.rows;
  std::vector<unsigned char> u8(
      static_cast<size_t>(output_rows) * static_cast<size_t>(output_cols));
  for (int src_row = 0; src_row < mosaic.amp.rows; ++src_row)
  {
    for (int src_col = 0; src_col < mosaic.amp.cols; ++src_col)
    {
      double v = mosaic.amp.at(src_row, src_col);
      if (v > th)
        v = th;
      // Keep the intermediate float conversion from the former path. It is
      // observable at half-integer quantization boundaries.
      const float normalized = static_cast<float>(v * (65500.0 / th));
      double quantized = normalized;
      if (quantized < 0.0)
        quantized = 0.0;
      if (quantized > 65535.0)
        quantized = 65535.0;
      const uint16_t gray16 = static_cast<uint16_t>(quantized + 0.5);
      const size_t output_index =
          static_cast<size_t>(src_col) * static_cast<size_t>(output_cols) +
          static_cast<size_t>(output_cols - 1 - src_row);
      u8[output_index] = static_cast<unsigned char>(gray16 >> 8);
    }
  }

  auto make_product_path = [&](int index, const char *ext) -> std::string {
    std::string d = cfg.result_add;
    if (!d.empty() && d.back() != '/' && d.back() != '\\')
      d.push_back('/');
    const std::string suffix = std::string(".") + ext;
    return d + gmti_product_name(index, suffix);
  };

  int idx = 0;
  if (cfg.result_file_id > 0)
  {
    std::string d = cfg.result_add;
    if (!d.empty() && d.back() != '/' && d.back() != '\\')
      d.push_back('/');
    if (!mkdir_p(d))
    {
      std::cerr << "writeProducts: 创建目录失败: " << d << "\n";
      return false;
    }
    idx = cfg.result_file_id;
  }
  else
  {
    std::string png_probe, txt_probe;
    int png_idx = 0, txt_idx = 0;
    if (!nextGMTIFileNamePNG(cfg.result_add, png_probe, png_idx))
      return false;
    if (!nextGMTIFileNameTxt(cfg.result_add, txt_probe, txt_idx))
      return false;
    idx = std::max(png_idx, txt_idx);
  }

  std::string outpath = make_product_path(idx, "png");

  if (!write_png_gray8(outpath, u8.data(), static_cast<unsigned>(output_cols),
                       static_cast<unsigned>(output_rows)))
    return false;

  // 4) 四角经纬度 .txt
  double B0, L0, B1, L1, B2, L2, B3, L3;
  proj_xy_to_ll(b.minX, b.minY, B0, L0);
  proj_xy_to_ll(b.maxX, b.maxY, B1, L1);
  proj_xy_to_ll(b.maxX, b.minY, B2, L2);
  proj_xy_to_ll(b.minX, b.maxY, B3, L3);

  outpath = make_product_path(idx, "txt");

  std::ofstream cfs(outpath.c_str(), std::ios::binary);
  if (!cfs)
    return false;
  cfs.setf(std::ios::fixed);
  cfs.precision(6);
  cfs << "B0 = " << B0 << "\n";
  cfs << "B1 = " << B1 << "\n";
  cfs << "L0 = " << L0 << "\n";
  cfs << "L1 = " << L1 << "\n";
  cfs << "B2 = " << B2 << "\n";
  cfs << "B3 = " << B3 << "\n";
  cfs << "L2 = " << L2 << "\n";
  cfs << "L3 = " << L3 << "\n";
  cfs.close();

  return true;
}

bool DbsStitcher::writeDebugMosaicImage(const Config &cfg,
                                        const Mosaic &mosaic,
                                        const std::string &filename)
{
  if (mosaic.amp.empty() || filename.empty())
    return false;

  Image2D<float> amp = mosaic.amp;
  const double th_src = mean_of_image(amp) * 13.0;
  const double th = (th_src > 1e-12) ? th_src : 1.0;
  for (size_t i = 0; i < amp.buf.size(); ++i)
  {
    double v = amp.buf[i];
    if (v > th)
      v = th;
    amp.buf[i] = static_cast<float>(v * (65500.0 / th));
  }

  Image2D<uint16_t> u16(amp.rows, amp.cols);
  for (size_t i = 0; i < amp.buf.size(); ++i)
  {
    double v = amp.buf[i];
    if (v < 0.0)
      v = 0.0;
    if (v > 65535.0)
      v = 65535.0;
    u16.buf[i] = static_cast<uint16_t>(v + 0.5);
  }

  Image2D<uint16_t> out16 = transpose_copy(u16);
  fliplr_inplace(out16);
  std::vector<unsigned char> u8(static_cast<size_t>(out16.rows) *
                                static_cast<size_t>(out16.cols));
  for (size_t i = 0; i < u8.size(); ++i)
    u8[i] = static_cast<unsigned char>(out16.buf[i] >> 8);

  std::string d = cfg.result_add;
  if (!d.empty() && d.back() != '/' && d.back() != '\\')
    d.push_back('/');
  if (!mkdir_p(d))
  {
    std::cerr << "writeDebugMosaicImage: 创建目录失败: " << d << "\n";
    return false;
  }
  const std::string path = d + filename;
  if (!write_png_gray8(path, u8.data(),
                       static_cast<unsigned>(out16.cols),
                       static_cast<unsigned>(out16.rows)))
    return false;
  if (cfg.runtime_diagnostics_enabled) {
    std::cout << "[fusion][dbs][debug] wrote " << path << std::endl;
  }
  return true;
}
