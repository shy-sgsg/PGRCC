#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <limits.h>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

#include "GMTIProcessor.hpp"
#include "pipe/MainCtrl.h"
#include "trig_lut.hpp"

using namespace std;

static atomic<bool> g_running{true};

void handle_signal(int)
{
    g_running.store(false);
}

namespace {

bool applyTrigArg(const string& arg, const char* next, bool& consumedNext)
{
    consumedNext = false;
    const string prefix = "--trig-mode=";
    if (arg.compare(0, prefix.size(), prefix) == 0) {
        return gmti::trig_lut::setModeFromString(arg.c_str() + prefix.size(), false);
    }
    if (arg == "--trig-mode") {
        if (!next) {
            cerr << "[ERR] --trig-mode 需要参数: lut|math|compare\n";
            return false;
        }
        consumedNext = true;
        return gmti::trig_lut::setModeFromString(next, false);
    }
    return true;
}

bool parseOnOff(const string& value, bool& out)
{
    string normalized = value;
    transform(normalized.begin(), normalized.end(), normalized.begin(),
              [](unsigned char ch) { return static_cast<char>(tolower(ch)); });
    if (normalized == "1" || normalized == "true" || normalized == "on") {
        out = true;
        return true;
    }
    if (normalized == "0" || normalized == "false" || normalized == "off") {
        out = false;
        return true;
    }
    return false;
}

string executableDirectory()
{
    char path[PATH_MAX + 1];
    const ssize_t n = readlink("/proc/self/exe", path, PATH_MAX);
    if (n <= 0 || n > PATH_MAX) return string();
    path[n] = '\0';
    string full(path);
    const size_t slash = full.find_last_of('/');
    return slash == string::npos ? string() : full.substr(0, slash);
}

string absolutePath(const string& path)
{
    char resolved[PATH_MAX + 1];
    if (realpath(path.c_str(), resolved)) return string(resolved);
    if (!path.empty() && path[0] == '/') return path;
    char cwd[PATH_MAX + 1];
    if (!getcwd(cwd, PATH_MAX)) return path;
    return string(cwd) + "/" + path;
}

string joinPath(const string& parent, const string& child)
{
    if (parent.empty() || parent == ".") return child;
    if (parent.back() == '/') return parent + child;
    return parent + "/" + child;
}

string makeLocalTestTrackDebugDir(const string& base)
{
    const auto now = chrono::system_clock::now();
    const time_t seconds = chrono::system_clock::to_time_t(now);
    const long long micros = chrono::duration_cast<chrono::microseconds>(
        now.time_since_epoch()).count() % 1000000LL;
    tm local_tm{};
    localtime_r(&seconds, &local_tm);
    ostringstream name;
    name << "local_test_" << put_time(&local_tm, "%Y%m%d_%H%M%S")
         << '_' << setw(6) << setfill('0') << micros
         << "_pid" << getpid();
    return joinPath(base, name.str());
}

void printUsage(const char* prog)
{
    cerr << "Usage:\n"
         << "  " << prog << " [--config PATH] [--echo-input-mode file|shm]"
         << " [--shm-name /NAME] [--pipe-root DIR] [--result-dir DIR]"
         << " [--track-debug-dir BASE_DIR]"
         << " [--track-debug-dump on|off]"
         << " [--runtime-diagnostics=on|off]"
         << " [--track-output-state-source measurement|prediction|kalman_filtered]"
         << " [--p38-diagnostics-dump on|off]"
         << " [--trig-mode lut|math|compare]"
         << " [--runtime-mode debug|release]\n"
         << "  " << prog << " [options] --local-test <xml> <echo1> [echo2 ...]\n"
         << "  " << prog << " [options] --config <xml> --local-test <echo1> [echo2 ...]\n"
         << "  " << prog << " [options] --local-test-loop [--local-test-max-cycles N] <xml> <echo>\n"
         << "  " << prog << " [options] --config <xml> --local-test-loop "
            "[--local-test-max-cycles N] <echo>\n"
         << "--local-test-loop repeats one complete echo file in the same process; "
            "N=0 means run until SIGINT/SIGTERM.\n"
         << "Echo specs may use <result_id>=<echo_path> or "
         << "<result_id>@<zero_based_period>=<multi_period_echo_path>.\n";
}

} // namespace

int main(int argc, char** argv)
{
    gmti::trig_lut::configureFromEnv();

    vector<string> args;
    string configPath;
    string echoInputOverride;
    string shmNameOverride;
    string pipeRootOverride;
    string resultDirOverride;
    string trackDebugDirOverride;
    bool trackDebugDumpOverrideSet = false;
    bool trackDebugDumpOverride = false;
    string trackOutputStateSourceOverride;
    bool p38DiagnosticsDumpOverrideSet = false;
    bool p38DiagnosticsDumpOverride = false;
    int shmCycleTimeoutOverride = -1;
    int localTestMaxCycles = 0;
    string runtimeModeOverride;
    bool diagnosticsOverrideSet = false;
    bool diagnosticsOverride = true;
    args.reserve(static_cast<size_t>(argc > 0 ? argc - 1 : 0));
    for (int i = 1; i < argc; ++i) {
        const string arg = argv[i];
        bool consumedNext = false;
        if (arg == "--trig-mode" ||
            arg.compare(0, string("--trig-mode=").size(), "--trig-mode=") == 0) {
            if (!applyTrigArg(arg, (i + 1 < argc) ? argv[i + 1] : nullptr, consumedNext)) {
                return 1;
            }
            if (consumedNext) ++i;
        } else if (arg.compare(0, string("--runtime-mode=").size(), "--runtime-mode=") == 0) {
            runtimeModeOverride = arg.substr(string("--runtime-mode=").size());
        } else if (arg == "--runtime-mode") {
            if (i + 1 >= argc) {
                cerr << "[ERR] --runtime-mode 需要参数: debug|release\n";
                return 1;
            }
            runtimeModeOverride = argv[++i];
        } else if (arg.compare(0, string("--runtime-diagnostics=").size(),
                               "--runtime-diagnostics=") == 0) {
            const string value = arg.substr(string("--runtime-diagnostics=").size());
            diagnosticsOverrideSet = true;
            diagnosticsOverride = value == "1" || value == "true" || value == "on";
        } else if (arg.compare(0, string("--config=").size(), "--config=") == 0) {
            configPath = arg.substr(string("--config=").size());
        } else if (arg == "--config") {
            if (i + 1 >= argc) {
                cerr << "[ERR] --config requires a path\n";
                return 1;
            }
            configPath = argv[++i];
        } else if (arg.compare(0, string("--echo-input-mode=").size(),
                               "--echo-input-mode=") == 0) {
            echoInputOverride = arg.substr(string("--echo-input-mode=").size());
        } else if (arg == "--echo-input-mode") {
            if (i + 1 >= argc) return 1;
            echoInputOverride = argv[++i];
        } else if (arg.compare(0, string("--shm-name=").size(), "--shm-name=") == 0) {
            shmNameOverride = arg.substr(string("--shm-name=").size());
        } else if (arg == "--shm-name") {
            if (i + 1 >= argc) return 1;
            shmNameOverride = argv[++i];
        } else if (arg.compare(0, string("--pipe-root=").size(), "--pipe-root=") == 0) {
            pipeRootOverride = arg.substr(string("--pipe-root=").size());
        } else if (arg == "--pipe-root") {
            if (i + 1 >= argc) return 1;
            pipeRootOverride = argv[++i];
        } else if (arg.compare(0, string("--result-dir=").size(), "--result-dir=") == 0) {
            resultDirOverride = arg.substr(string("--result-dir=").size());
        } else if (arg == "--result-dir") {
            if (i + 1 >= argc) return 1;
            resultDirOverride = argv[++i];
        } else if (arg.compare(0, string("--track-debug-dir=").size(),
                               "--track-debug-dir=") == 0) {
            trackDebugDirOverride =
                arg.substr(string("--track-debug-dir=").size());
        } else if (arg == "--track-debug-dir") {
            if (i + 1 >= argc) return 1;
            trackDebugDirOverride = argv[++i];
        } else if (arg.compare(0, string("--track-debug-dump=").size(),
                               "--track-debug-dump=") == 0) {
            trackDebugDumpOverrideSet = true;
            if (!parseOnOff(
                    arg.substr(string("--track-debug-dump=").size()),
                    trackDebugDumpOverride)) {
                cerr << "[ERR] --track-debug-dump requires on|off\n";
                return 1;
            }
        } else if (arg == "--track-debug-dump") {
            if (i + 1 >= argc ||
                !parseOnOff(argv[++i], trackDebugDumpOverride)) {
                cerr << "[ERR] --track-debug-dump requires on|off\n";
                return 1;
            }
            trackDebugDumpOverrideSet = true;
        } else if (arg.compare(
                       0, string("--track-output-state-source=").size(),
                       "--track-output-state-source=") == 0) {
            trackOutputStateSourceOverride =
                arg.substr(string("--track-output-state-source=").size());
        } else if (arg == "--track-output-state-source") {
            if (i + 1 >= argc) return 1;
            trackOutputStateSourceOverride = argv[++i];
        } else if (arg.compare(0, string("--p38-diagnostics-dump=").size(),
                               "--p38-diagnostics-dump=") == 0) {
            p38DiagnosticsDumpOverrideSet = true;
            if (!parseOnOff(
                    arg.substr(string("--p38-diagnostics-dump=").size()),
                    p38DiagnosticsDumpOverride)) {
                cerr << "[ERR] --p38-diagnostics-dump requires on|off\n";
                return 1;
            }
        } else if (arg == "--p38-diagnostics-dump") {
            if (i + 1 >= argc ||
                !parseOnOff(argv[++i], p38DiagnosticsDumpOverride)) {
                cerr << "[ERR] --p38-diagnostics-dump requires on|off\n";
                return 1;
            }
            p38DiagnosticsDumpOverrideSet = true;
        } else if (arg.compare(0, string("--shm-cycle-timeout-ms=").size(),
                               "--shm-cycle-timeout-ms=") == 0) {
            shmCycleTimeoutOverride = stoi(arg.substr(string("--shm-cycle-timeout-ms=").size()));
        } else if (arg == "--shm-cycle-timeout-ms") {
            if (i + 1 >= argc) return 1;
            shmCycleTimeoutOverride = stoi(argv[++i]);
        } else if (arg.compare(0, string("--local-test-max-cycles=").size(),
                               "--local-test-max-cycles=") == 0) {
            try {
                localTestMaxCycles = stoi(
                    arg.substr(string("--local-test-max-cycles=").size()));
            } catch (const std::exception&) {
                cerr << "[ERR] --local-test-max-cycles requires a non-negative integer\n";
                return 1;
            }
        } else if (arg == "--local-test-max-cycles") {
            if (i + 1 >= argc) {
                cerr << "[ERR] --local-test-max-cycles requires a non-negative integer\n";
                return 1;
            }
            try {
                localTestMaxCycles = stoi(argv[++i]);
            } catch (const std::exception&) {
                cerr << "[ERR] --local-test-max-cycles requires a non-negative integer\n";
                return 1;
            }
        } else {
            args.push_back(arg);
        }
    }

    transform(runtimeModeOverride.begin(), runtimeModeOverride.end(),
              runtimeModeOverride.begin(),
              [](unsigned char ch) { return static_cast<char>(tolower(ch)); });

    bool localTest = false;
    bool localTestLoop = false;
    vector<string> echoFiles;
    if (!args.empty() &&
        (args[0] == "--local-test" || args[0] == "--local-test-loop")) {
        localTest = true;
        localTestLoop = args[0] == "--local-test-loop";
        size_t echoStart = 1U;
        if (configPath.empty()) {
            if (args.size() < 3U) {
                printUsage(argv[0]);
                return 1;
            }
            configPath = args[1];
            echoStart = 2U;
        } else if (args.size() < 2U) {
            printUsage(argv[0]);
            return 1;
        }
        echoFiles.assign(args.begin() + static_cast<ptrdiff_t>(echoStart), args.end());
        if (localTestLoop && echoFiles.size() != 1U) {
            cerr << "[LOCAL-TEST][ERR] --local-test-loop requires exactly one echo file\n";
            return 1;
        }
    } else if (!args.empty()) {
        printUsage(argv[0]);
        return 1;
    }
    if (localTestMaxCycles < 0) {
        cerr << "[LOCAL-TEST][ERR] --local-test-max-cycles must be non-negative\n";
        return 1;
    }
    if (localTestMaxCycles != 0 && !localTestLoop) {
        cerr << "[LOCAL-TEST][ERR] --local-test-max-cycles is only valid with "
                "--local-test-loop\n";
        return 1;
    }

    if (configPath.empty()) {
        const string exeDir = executableDirectory();
        if (exeDir.empty()) {
            cerr << "[STARTUP][ERR] cannot resolve /proc/self/exe for default config\n";
            return 1;
        }
        configPath = exeDir + "/gmti.xml";
    }
    configPath = absolutePath(configPath);

    // XML parsing is deliberately before MainCtrl construction and before any
    // FIFO, shared-memory mapping, cycle allocation, or worker thread.
    Config initialConfig;
    if (!runtimeModeOverride.empty()) {
        initialConfig.runtime_mode = runtimeModeOverride;
        initialConfig.runtime_diagnostics_enabled =
            runtimeModeOverride != "release" &&
            runtimeModeOverride != "formal" &&
            runtimeModeOverride != "production";
    }
    if (diagnosticsOverrideSet) {
        initialConfig.runtime_diagnostics_enabled = diagnosticsOverride;
    }
    if (localTest && !echoFiles.empty()) {
        const string& firstSpec = echoFiles.front();
        const size_t equals = firstSpec.find('=');
        const string firstEchoPath =
            equals == string::npos ? firstSpec : firstSpec.substr(equals + 1U);
        initialConfig.GMTI_Data_new = firstEchoPath;
        initialConfig.GMTI_Data_add = firstEchoPath;
    }
    GMTIProcessor configReader;
    if (!configReader.readXmlParam(configPath, initialConfig)) {
        cerr << "[STARTUP][ERR] XML parse failed before thread creation: "
             << configPath << endl;
        return 1;
    }
    if (!echoInputOverride.empty()) initialConfig.echo_input_mode = echoInputOverride;
    if (!shmNameOverride.empty()) initialConfig.shm_name = shmNameOverride;
    if (!pipeRootOverride.empty()) initialConfig.pipe_root_path = absolutePath(pipeRootOverride);
    if (!resultDirOverride.empty()) initialConfig.result_add = absolutePath(resultDirOverride);
    if (shmCycleTimeoutOverride >= 0) {
        initialConfig.shm_cycle_timeout_ms = shmCycleTimeoutOverride;
    }
    if (!trackOutputStateSourceOverride.empty()) {
        initialConfig.track_output_state_source = trackOutputStateSourceOverride;
    }
    if (trackDebugDumpOverrideSet) {
        initialConfig.track_debug_dump = trackDebugDumpOverride;
    }
    if (p38DiagnosticsDumpOverrideSet) {
        initialConfig.p38_diagnostics_dump = p38DiagnosticsDumpOverride;
    }
    if (!runtimeModeOverride.empty()) {
        initialConfig.runtime_mode = runtimeModeOverride;
        initialConfig.runtime_diagnostics_enabled =
            runtimeModeOverride != "release" &&
            runtimeModeOverride != "formal" &&
            runtimeModeOverride != "production";
    }
    if (diagnosticsOverrideSet) {
        initialConfig.runtime_diagnostics_enabled = diagnosticsOverride;
    }
    if (localTest) {
        string requestedEchoMode = initialConfig.echo_input_mode;
        transform(requestedEchoMode.begin(), requestedEchoMode.end(),
                  requestedEchoMode.begin(),
                  [](unsigned char ch) { return static_cast<char>(tolower(ch)); });
        if (!echoInputOverride.empty() && requestedEchoMode != "file") {
            cerr << "[LOCAL-TEST][ERR] --local-test/--local-test-loop only accepts "
                    "file echo input; "
                 << "shared memory is disabled in this mode\n";
            return 1;
        }
        initialConfig.echo_input_mode = "file";

        if (initialConfig.track_debug_dump) {
            string trackDebugBase = trackDebugDirOverride.empty()
                ? initialConfig.track_debug_dir
                : trackDebugDirOverride;
            if (trackDebugBase.empty()) {
                trackDebugBase = joinPath(initialConfig.result_add, "track_debug_runs");
            }
            trackDebugBase = absolutePath(trackDebugBase);
            initialConfig.track_debug_dir = makeLocalTestTrackDebugDir(trackDebugBase);
            cout << "[LOCAL-TEST] track_debug=" << initialConfig.track_debug_dir << endl;
        } else {
            initialConfig.track_debug_dir.clear();
        }
    } else if (!trackDebugDirOverride.empty()) {
        initialConfig.track_debug_dir = absolutePath(trackDebugDirOverride);
    }

    // 无参部署入口始终对接总体主控的共享内存。XML <test> 只决定在
    // 一个采集周期就绪后，算法使用共享内存回波（0）、不透明字节计数（1）
    // 或旁路协议审查后的字节计数（2）。
    // 显式 --echo-input-mode 仅保留给诊断/兼容调用；真正无参启动固定走此路径。
    if (!localTest && echoInputOverride.empty()) {
        initialConfig.echo_input_mode = "shm";
    }

    // Emit the resolved deployment contract before any CUDA initialization.
    // This keeps a no-argument startup diagnosable even on a host where the
    // accelerator is unavailable: default configuration selection is not
    // allowed to depend on the shell working directory or on CUDA success.
    if (initialConfig.test_mode != 0 && initialConfig.test_mode != 1 &&
        initialConfig.test_mode != 2) {
        cerr << "[STARTUP][ERR] <test> must be 0 (real), 1 (opaque laboratory), "
             << "or 2 (audited laboratory), got "
             << initialConfig.test_mode << '\n';
        return 1;
    }
    const string startupInputMode = initialConfig.echo_input_mode == "shm"
        ? "shared_memory" : initialConfig.echo_input_mode;
    cout << "[STARTUP] runtime_mode=" << initialConfig.runtime_mode << '\n'
         << "[STARTUP] config_path=" << configPath << '\n'
         << "[STARTUP] test=" << initialConfig.test_mode << '\n'
         << "[STARTUP] input_mode=" << startupInputMode << endl;

    if (!gmti::trig_lut::initialize(true)) {
        cerr << "[ERR] 三角函数路径初始化失败\n";
        return 1;
    }
    if (initialConfig.runtime_diagnostics_enabled) {
        gmti::trig_lut::benchmark(1u << 22);
    }

    MainCtrl gmtiCtrl(initialConfig, configPath, !localTest);
    gmtiCtrl.runtime_mode_override_ = runtimeModeOverride;
    gmtiCtrl.runtime_diagnostics_override_set_ = diagnosticsOverrideSet;
    gmtiCtrl.runtime_diagnostics_override_ = diagnosticsOverride;
    if (!gmtiCtrl.Init()) {
        cerr << "[STARTUP][ERR] MainCtrl initialization failed; no processing started\n";
        return 1;
    }

    if (localTest) {
        return gmtiCtrl.RunLocalTest(
            echoFiles, localTestLoop, localTestMaxCycles) ? 0 : 1;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    while (g_running.load()) sleep(1);

    cout << "GMTI exiting..." << endl;
    gmtiCtrl.StopThreads();
    return 0;
}
