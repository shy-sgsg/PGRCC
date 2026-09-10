cmake_minimum_required(VERSION 3.16)

# ============================================================
# 0. Build mode
# ============================================================
# OFF: normal CUDA/nvcc build, for your own CUDA development PC
# ON : CoreX clang++ ivcore build, for CoreX environment
option(USE_COREX "Build with CoreX clang++ ivcore instead of native CUDA/nvcc" OFF)

set(COREX_ROOT "/usr/local/corex-4.3.7" CACHE PATH "CoreX installation root")

if(USE_COREX)
  set(CMAKE_CXX_COMPILER "${COREX_ROOT}/bin/clang++" CACHE FILEPATH "CoreX clang++ compiler" FORCE)
  project(GMTI_core LANGUAGES CXX)
else()
  if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
    set(CMAKE_CUDA_ARCHITECTURES 75 CACHE STRING "CUDA architectures")
  endif()
  project(GMTI_core LANGUAGES CXX CUDA)
endif()

include(CTest)

# ============================================================
# 1. Compile options
# ============================================================
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT USE_COREX)
  set(CMAKE_CUDA_SEPARABLE_COMPILATION ON)
endif()

set(LIBOMP_ROOT "/opt/homebrew/opt/libomp")

if(APPLE)
  set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "" FORCE)
endif()

if(MSVC)
  add_compile_options(/W4 /permissive-)
else()
  add_compile_options(
    $<$<COMPILE_LANGUAGE:CXX>:-Wall>
    $<$<COMPILE_LANGUAGE:CXX>:-Wextra>
    $<$<COMPILE_LANGUAGE:CXX>:-Wpedantic>
  )
endif()

option(ENABLE_OPENMP "Enable OpenMP parallelization" OFF)
option(ENABLE_DEBUG_LOG "Force define DEBUG macro for verbose logging" OFF)

# CoreX 模式默认关闭 texture object 路径
# Native CUDA 模式默认允许 texture object 路径
if(USE_COREX)
  set(DEFAULT_DBS_TEXTURE_OBJECTS OFF)
else()
  set(DEFAULT_DBS_TEXTURE_OBJECTS ON)
endif()

option(DBS_USE_CUDA_TEXTURE_OBJECTS
  "Use legacy CUDA texture-object DBS stitcher path"
  ${DEFAULT_DBS_TEXTURE_OBJECTS}
)

if(USE_COREX AND DBS_USE_CUDA_TEXTURE_OBJECTS)
  message(WARNING
    "USE_COREX=ON but DBS_USE_CUDA_TEXTURE_OBJECTS=ON. "
    "CoreX ivcore may fail on tex2D/cudaTextureObject_t. "
    "Consider using -DDBS_USE_CUDA_TEXTURE_OBJECTS=OFF."
  )
endif()

if(DBS_USE_CUDA_TEXTURE_OBJECTS)
  set(DBS_TEXTURE_DEFINE DBS_USE_CUDA_TEXTURE_OBJECTS=1)
else()
  set(DBS_TEXTURE_DEFINE DBS_USE_CUDA_TEXTURE_OBJECTS=0)
endif()

# ============================================================
# 2. Dependencies
# ============================================================

if(NOT USE_COREX)
  find_package(CUDAToolkit REQUIRED)
endif()

if(ENABLE_OPENMP)
  find_package(OpenMP)
endif()

find_package(Threads REQUIRED)
find_package(TIFF REQUIRED)

# FFTW3
set(FFTW3_TARGET "")
set(FFTW3_THREADS_TARGET "")
set(FFTW3F_TARGET "")

find_package(FFTW3 QUIET COMPONENTS FFTW3 FFTW3_THREADS)

if(FFTW3_FOUND)
  if(TARGET FFTW3::FFTW3)
    add_library(FFTW3::fftw3 ALIAS FFTW3::FFTW3)
  endif()

  if(TARGET FFTW3::FFTW3_THREADS)
    add_library(FFTW3::fftw3_threads ALIAS FFTW3::FFTW3_THREADS)
  endif()
endif()

if(TARGET FFTW3::fftw3)
  set(FFTW3_TARGET FFTW3::fftw3)
elseif(TARGET fftw3::fftw3)
  add_library(FFTW3::fftw3 ALIAS fftw3::fftw3)
  set(FFTW3_TARGET FFTW3::fftw3)
endif()

if(TARGET FFTW3::fftw3_threads)
  set(FFTW3_THREADS_TARGET FFTW3::fftw3_threads)
elseif(TARGET fftw3::fftw3_threads)
  add_library(FFTW3::fftw3_threads ALIAS fftw3::fftw3_threads)
  set(FFTW3_THREADS_TARGET FFTW3::fftw3_threads)
endif()

if(NOT FFTW3_TARGET)
  find_library(FFTW3_LIB
    NAMES fftw3
    PATHS
      /usr/lib64
      /usr/lib
      /usr/local/lib
      /usr/local/lib64
      /opt/homebrew/lib
  )

  if(FFTW3_LIB)
    add_library(FFTW3::fftw3 INTERFACE IMPORTED)
    target_link_libraries(FFTW3::fftw3 INTERFACE "${FFTW3_LIB}")
    set(FFTW3_TARGET FFTW3::fftw3)
  else()
    set(FFTW3_TARGET fftw3)
  endif()
endif()

# Single-precision FFTW is used by the target-profile echo generator.
find_library(FFTW3F_LIB
  NAMES fftw3f
  PATHS
    /usr/lib64
    /usr/lib
    /usr/lib/x86_64-linux-gnu
    /usr/local/lib
    /usr/local/lib64
    /opt/homebrew/lib
)

if(FFTW3F_LIB)
  add_library(FFTW3::fftw3f INTERFACE IMPORTED)
  target_link_libraries(FFTW3::fftw3f INTERFACE "${FFTW3F_LIB}")
  set(FFTW3F_TARGET FFTW3::fftw3f)
else()
  set(FFTW3F_TARGET fftw3f)
endif()

# ============================================================
# 3. Sources
# ============================================================

set(GPU_CUDA_SRC
  src/gpu/trig_lut_device.cu
  src/gpu/rangeCompress_cu.cu
  src/gpu/rangeCompress_device.cu
  src/gpu/gpu_kernels.cu
)

set(DBS_CUDA_SRC
  src/dbs/stitcher_kernel.cu
  src/dbs/stitcher_kernel_texarray.cu
)

set(STAGE3_CUDA_SRC
  simulator/stage3_sar_scene_sim/stage3_cuda_forward.cu
)

set(DBS_FUSION_SRC
  src/dbs/DbsStitcher.cpp
  src/dbs/buildMosaic.cpp
  src/dbs/lodepng.cpp
  ${DBS_CUDA_SRC}
)

set(XML_SRC
   src/xml/tinystr.cpp
   src/xml/tinyxml.cpp
   src/xml/tinyxmlerror.cpp
   src/xml/tinyxmlparser.cpp
)

set(AUTH_SRC
   auth/hardware_bind_flow/core/binding.cpp
   auth/hardware_bind_flow/gate/checkpoint_codec.cpp
   auth/hardware_bind_flow/gate/security_gate.cpp
   auth/shared/crypto/sm3.cpp
   auth/shared/hardware/hardware.cpp
)

set(COMMON_CORE_SRC
  src/alignFFTAndDBS.cpp
  src/trig_lut.cpp
  src/clutter_CSI.cpp
  src/csi_metrics_tap.cpp
  src/computeDynamicSupportDomain.cpp
  src/detectAndLocating.cpp
  src/loadXML.cpp
  src/low_radial_velocity_filter.cpp
  src/mechanical_scan.cpp
  src/dbs/DbsFusion.cpp
  src/dbs/NewProtocolReader.cpp
  src/processOnePeriod.cpp
  src/processPeriodsParallel.cpp
  src/pulseCompression.cpp
  src/rg_correct.cpp
  src/runtime_diagnostics.cpp
  src/writeresults.cpp
  src/kalman_track.cpp
  src/TrackManager.cpp
  src/trackModule.cpp
  src/extractUTC.cpp
  src/makeLists.cpp
  src/pesudoTarget_Gen.cpp
  simulator/common/SimulationGeometry.cpp
  ${XML_SRC}
  ${GPU_CUDA_SRC}
  ${DBS_FUSION_SRC}
)

set(GMTI_PIPE_ONLY_SRC
  src/pipe/main.cpp
  src/pipe/MainCtrl.cpp
  src/pipe/PipeRW.cpp
  src/pipe/ShmEchoInput.cpp
)

set(ALL_CUDA_SRC
  ${GPU_CUDA_SRC}
  ${DBS_CUDA_SRC}
  ${STAGE3_CUDA_SRC}
)

# CoreX 模式：不启用 CUDA language，把 .cu 当作 CXX 交给 clang++ -x ivcore
if(USE_COREX)
  set_source_files_properties(${ALL_CUDA_SRC} PROPERTIES
    LANGUAGE CXX
    COMPILE_FLAGS "-x ivcore --cuda-path=${COREX_ROOT} --cuda-gpu-arch=ivcore11 -fPIC"
  )
endif()

# ============================================================
# 4. Targets
# ============================================================

add_executable(GMTI_core
  src/main.cpp
  ${COMMON_CORE_SRC}
  ${AUTH_SRC}
)

add_executable(convert_old_echo_to_new_format
  src/tools/convert_old_echo_to_new_format.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/pulseCompression.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(generate_target_profile_echo
  src/tools/generate_target_profile_echo.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/pulseCompression.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(generate_reflectivity_forward_echo
  src/tools/generate_reflectivity_forward_echo.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
  src/dbs/lodepng.cpp
)

add_executable(simulate_stage1_background
  simulator/stage1_background_sim/simulate_stage1_main.cpp
  simulator/stage1_background_sim/stage1_config.cpp
  simulator/stage1_background_sim/stage1_data_reader.cpp
  simulator/stage1_background_sim/stage1_beam_mapper.cpp
  simulator/stage1_background_sim/stage1_pulse_resampler.cpp
  simulator/stage1_background_sim/stage1_range_resizer.cpp
  simulator/stage1_background_sim/stage1_writer.cpp
  simulator/stage1_background_sim/stage1_validator.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(inject_cooperative_target
  simulator/target_injection/inject_target_main.cpp
  simulator/target_injection/target_common.cpp
  simulator/target_injection/target_config.cpp
  simulator/target_injection/target_motion_model.cpp
  simulator/target_injection/radar_geometry.cpp
  simulator/target_injection/beam_visibility.cpp
  simulator/target_injection/channel_phase_model.cpp
  simulator/target_injection/channel_impairments.cpp
  simulator/target_injection/lfm_echo_generator.cpp
  simulator/target_injection/truth_writer.cpp
  simulator/common/SimulationGeometry.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(simulate_stage2_statistical
  simulator/stage2_statistical_sim/simulate_stage2_main.cpp
  simulator/stage2_statistical_sim/stage2_config.cpp
  simulator/stage2_statistical_sim/stage2_scene_generator.cpp
  simulator/stage2_statistical_sim/stage2_lfm_forward.cpp
  simulator/stage2_statistical_sim/stage2_validator.cpp
  simulator/common/SimulationGeometry.cpp
  simulator/target_injection/target_common.cpp
  simulator/target_injection/target_config.cpp
  simulator/target_injection/target_motion_model.cpp
  simulator/target_injection/radar_geometry.cpp
  simulator/target_injection/beam_visibility.cpp
  simulator/target_injection/channel_phase_model.cpp
  simulator/target_injection/channel_impairments.cpp
  simulator/target_injection/lfm_echo_generator.cpp
  simulator/target_injection/truth_writer.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(dump_newprotocol_header
  tools/dump_newprotocol_header.cpp
)

add_executable(simulate_stage3_sar_scene
  simulator/stage3_sar_scene_sim/simulate_stage3_main.cpp
  simulator/stage3_sar_scene_sim/stage3_config.cpp
  simulator/stage3_sar_scene_sim/stage3_scene_mapping.cpp
  simulator/stage3_sar_scene_sim/sar_image_reader.cpp
  simulator/stage3_sar_scene_sim/sar_georef.cpp
  simulator/stage3_sar_scene_sim/sar_radiometric_normalizer.cpp
  simulator/stage3_sar_scene_sim/sar_scatterer_extractor.cpp
  simulator/stage3_sar_scene_sim/sar_scatterer_sampler.cpp
  simulator/stage3_sar_scene_sim/sar_scene_forward.cpp
  ${STAGE3_CUDA_SRC}
  simulator/stage3_sar_scene_sim/stage3_validator.cpp
  simulator/common/radar_geometry.cpp
  simulator/common/lfm_echo_generator.cpp
  simulator/common/beam_pattern.cpp
  simulator/common/channel_phase_model.cpp
  simulator/common/truth_writer.cpp
  # Reuse the Stage2 raw-LFM cooperative-target model so Stage3 SAR clutter
  # and moving targets are combined before the packet is written.
  simulator/target_injection/target_common.cpp
  simulator/target_injection/target_config.cpp
  simulator/target_injection/target_motion_model.cpp
  simulator/target_injection/radar_geometry.cpp
  simulator/target_injection/beam_visibility.cpp
  simulator/target_injection/channel_phase_model.cpp
  simulator/target_injection/channel_impairments.cpp
  simulator/target_injection/lfm_echo_generator.cpp
  simulator/target_injection/truth_writer.cpp
  simulator/common/SimulationGeometry.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
  src/xml/tinystr.cpp
  src/xml/tinyxml.cpp
  src/xml/tinyxmlerror.cpp
  src/xml/tinyxmlparser.cpp
)

add_executable(GMTI_pipe_core
  ${GMTI_PIPE_ONLY_SRC}
  ${COMMON_CORE_SRC}
  ${AUTH_SRC}
)

add_custom_target(gmti_default_config ALL
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
          ${CMAKE_SOURCE_DIR}/gmti.xml
          ${CMAKE_CURRENT_BINARY_DIR}/gmti.xml
  DEPENDS ${CMAKE_SOURCE_DIR}/gmti.xml
  COMMENT "Copy root gmti.xml next to GMTI_pipe_core"
)
add_dependencies(GMTI_pipe_core gmti_default_config)

add_executable(track_assignment_selftest
  tests/track_assignment_selftest.cpp
  src/TrackManager.cpp
  src/runtime_diagnostics.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
)

add_executable(low_radial_velocity_filter_selftest
  tests/low_radial_velocity_filter_selftest.cpp
  src/low_radial_velocity_filter.cpp
)

# Detection-level P9 harness.  It invokes the production TrackManager and only
# replaces the upstream detector with a traceable CSV fault-injection source.
add_executable(track_detection_eval
  src/tools/track_detection_eval.cpp
  src/TrackManager.cpp
  src/runtime_diagnostics.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
)

add_executable(range_compression_window_selftest
  tests/range_compression_window_selftest.cpp
  src/pulseCompression.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
)

add_executable(motion_comp_ctdr_selftest
  src/tools/motion_comp_ctdr_selftest.cpp
)

add_executable(velocity_ambiguity_selftest
  tests/velocity_ambiguity_selftest.cpp
)

add_executable(channel_calibration_selftest
  tests/channel_calibration_selftest.cpp
)

add_executable(velocity_ambiguity_sweep
  src/tools/velocity_ambiguity_sweep.cpp
)

add_executable(p38_phase_fit_selftest
  tests/p38_phase_fit_selftest.cpp
)

add_executable(imu_interpolation_selftest
  tests/imu_interpolation_selftest.cpp
)

add_executable(trig_lut_selftest
  tests/trig_lut_selftest.cpp
  src/trig_lut.cpp
  src/trig_lut_host_only.cpp
)

add_executable(stage3_scene_mapping_selftest
  tests/stage3_scene_mapping_selftest.cpp
  simulator/stage3_sar_scene_sim/stage3_scene_mapping.cpp
)

add_executable(beam_visibility_selftest
  tests/beam_visibility_selftest.cpp
  simulator/target_injection/beam_visibility.cpp
)

add_executable(truth_writer_midpoint_selftest
  tests/truth_writer_midpoint_selftest.cpp
  simulator/target_injection/truth_writer.cpp
  simulator/target_injection/target_common.cpp
)

add_executable(new_protocol_layout_selftest
  tests/new_protocol_layout_selftest.cpp
)

add_executable(mechanical_scan_selftest
  tests/mechanical_scan_selftest.cpp
  src/mechanical_scan.cpp
)

add_executable(mechanical_phase_center_selftest
  tests/mechanical_phase_center_selftest.cpp
)

add_executable(mechanical_protocol_reader_selftest
  tests/mechanical_protocol_reader_selftest.cpp
  src/dbs/NewProtocolReader.cpp
  src/mechanical_scan.cpp
)

add_executable(shm_echo_input_selftest
  tests/shm_echo_input_selftest.cpp
  src/pipe/ShmEchoInput.cpp
  src/dbs/NewProtocolReader.cpp
  src/mechanical_scan.cpp
)

if(NOT USE_COREX)
  add_executable(csi_gate_cpu_selftest
    tests/csi_gate_cpu_selftest.cpp
    src/clutter_CSI.cpp
  )
endif()

add_executable(shm_echo_producer
  src/tools/shm_echo_producer.cpp
)

add_executable(gmti_pipe_test_client
  src/tools/gmti_pipe_test_client.cpp
)

add_executable(shm_echo_capture
  src/tools/shm_echo_capture.cpp
  src/pipe/ShmEchoInput.cpp
)

# Keep the supplied shmdemo producer/consumer buildable as standalone
# interoperability tools.  GMTI consumes this ABI; these targets do not
# replace or modify the reference implementation.
add_executable(shmdemo_sender
  shmdemo/demo/sender.cpp
)

add_executable(shmdemo_receiver
  shmdemo/demo/receiver.cpp
)

# ============================================================
# 5. Helper variables
# ============================================================

set(PROJECT_INCLUDE_DIRS
  ${CMAKE_SOURCE_DIR}/include
  ${CMAKE_SOURCE_DIR}/include/xml
  auth/shared/hardware
  auth/shared/crypto
  auth/hardware_bind_flow/core
  auth/hardware_bind_flow/gate
)

set(PIPE_INCLUDE_DIRS
  ${CMAKE_SOURCE_DIR}/include/pipe
  ${CMAKE_SOURCE_DIR}/src/pipe
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
  auth/shared/hardware
  auth/shared/crypto
  auth/hardware_bind_flow/core
  auth/hardware_bind_flow/gate
)

if(USE_COREX)
  list(APPEND PROJECT_INCLUDE_DIRS
    ${COREX_ROOT}/include
    /usr/local/include/fftw_include
  )
else()
  list(APPEND PROJECT_INCLUDE_DIRS
    ${CUDAToolkit_INCLUDE_DIRS}
  )
endif()

set(SYSTEM_LINK_LIBS
  Threads::Threads
  m
)

if(UNIX AND NOT APPLE)
  list(APPEND SYSTEM_LINK_LIBS rt dl)
endif()

# CMake may use the CUDA toolkit's host linker search directory (for example
# GCC 13) ahead of the active C++ compiler's runtime directory (for example
# GCC 15).  Objects produced by the newer compiler can then reference C++ ABI
# symbols that the older libstdc++ does not provide.  Resolve the active host
# compiler's runtime directory once and put it first for CUDA-bearing targets.
# This keeps the fix tied to the selected compiler instead of hard-coding a
# distro-specific GCC version.
set(GMTI_HOST_CXX_RUNTIME_DIR "")
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND NOT USE_COREX)
  execute_process(
    COMMAND ${CMAKE_CXX_COMPILER} -print-file-name=libstdc++.so
    OUTPUT_VARIABLE GMTI_HOST_LIBSTDCXX
    OUTPUT_STRIP_TRAILING_WHITESPACE
  )
  if(EXISTS "${GMTI_HOST_LIBSTDCXX}")
    get_filename_component(GMTI_HOST_CXX_RUNTIME_DIR
                           "${GMTI_HOST_LIBSTDCXX}" DIRECTORY)
  endif()
endif()

# ============================================================
# 6. Target configure functions
# ============================================================

function(configure_gmti_target target_name)
  target_include_directories(${target_name} PRIVATE
    ${PROJECT_INCLUDE_DIRS}
  )

  target_compile_definitions(${target_name} PRIVATE
    ${DBS_TEXTURE_DEFINE}
  )

  target_link_libraries(${target_name} PRIVATE
    ${FFTW3_TARGET}
    ${SYSTEM_LINK_LIBS}
  )

  if(GMTI_HOST_CXX_RUNTIME_DIR)
    # `target_link_directories` may elide an implicit C++ directory.  Keep an
    # explicit -L option because the conflicting directory is injected later
    # by CUDA's host-link metadata.
    target_link_options(${target_name} BEFORE PRIVATE
      "-L${GMTI_HOST_CXX_RUNTIME_DIR}"
    )
  endif()

  if(FFTW3_THREADS_TARGET)
    target_link_libraries(${target_name} PRIVATE ${FFTW3_THREADS_TARGET})
  endif()

  if(USE_COREX)
    target_link_directories(${target_name} PRIVATE
      ${COREX_ROOT}/lib
      ${COREX_ROOT}/lib64
      /usr/local/cuda/lib64
    )

    target_link_libraries(${target_name} PRIVATE
      cudart
      cuda
      cufft
    )

    set_target_properties(${target_name} PROPERTIES
      BUILD_RPATH "${COREX_ROOT}/lib;${COREX_ROOT}/lib64"
      INSTALL_RPATH "${COREX_ROOT}/lib;${COREX_ROOT}/lib64"
    )
  else()
    target_link_libraries(${target_name} PRIVATE
      CUDA::cudart
      CUDA::cufft
    )

    target_compile_options(${target_name} PRIVATE
      $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math>
      $<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=-fPIC>
    )
  endif()

  if(ENABLE_OPENMP)
    if(APPLE AND OpenMP_CXX_FOUND)
      target_compile_options(${target_name} PRIVATE -Xpreprocessor -fopenmp)
      target_link_libraries(${target_name} PRIVATE "${LIBOMP_ROOT}/lib/libomp.dylib")
      target_include_directories(${target_name} PRIVATE "${LIBOMP_ROOT}/include")
    elseif(OpenMP_CXX_FOUND)
      target_link_libraries(${target_name} PRIVATE OpenMP::OpenMP_CXX)
    endif()
  endif()

  if(ENABLE_DEBUG_LOG)
    target_compile_definitions(${target_name} PRIVATE DEBUG)
  else()
    target_compile_definitions(${target_name} PRIVATE
      $<$<CONFIG:Debug>:DEBUG>
      $<$<CONFIG:Release>:NDEBUG>
      $<$<CONFIG:RelWithDebInfo>:NDEBUG>
    )
  endif()

  if(NOT MSVC)
    target_compile_options(${target_name} PRIVATE
      $<$<CONFIG:Debug>:-O0 -g3 -fno-omit-frame-pointer>
      $<$<CONFIG:Release>:-O3>
    )
  endif()
endfunction()

function(configure_non_cuda_tool target_name)
  target_include_directories(${target_name} PRIVATE
    ${PROJECT_INCLUDE_DIRS}
  )

  target_link_libraries(${target_name} PRIVATE
    ${FFTW3_TARGET}
    ${SYSTEM_LINK_LIBS}
  )

  if(FFTW3_THREADS_TARGET)
    target_link_libraries(${target_name} PRIVATE ${FFTW3_THREADS_TARGET})
  endif()

  if(USE_COREX)
    target_link_directories(${target_name} PRIVATE
      ${COREX_ROOT}/lib
      ${COREX_ROOT}/lib64
      /usr/local/cuda/lib64
    )
  endif()

  if(ENABLE_DEBUG_LOG)
    target_compile_definitions(${target_name} PRIVATE DEBUG)
  else()
    target_compile_definitions(${target_name} PRIVATE
      $<$<CONFIG:Debug>:DEBUG>
      $<$<CONFIG:Release>:NDEBUG>
      $<$<CONFIG:RelWithDebInfo>:NDEBUG>
    )
  endif()

  if(NOT MSVC)
    target_compile_options(${target_name} PRIVATE
      $<$<CONFIG:Debug>:-O0 -g3 -fno-omit-frame-pointer>
      $<$<CONFIG:Release>:-O3>
    )
  endif()
endfunction()

# ============================================================
# 7. Apply target config
# ============================================================

configure_gmti_target(GMTI_core)
configure_gmti_target(GMTI_pipe_core)

target_include_directories(GMTI_pipe_core PRIVATE
  ${PIPE_INCLUDE_DIRS}
)

configure_non_cuda_tool(convert_old_echo_to_new_format)
configure_non_cuda_tool(generate_target_profile_echo)
target_link_libraries(generate_target_profile_echo PRIVATE ${FFTW3F_TARGET})
configure_non_cuda_tool(generate_reflectivity_forward_echo)
configure_non_cuda_tool(simulate_stage1_background)
target_include_directories(simulate_stage1_background PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/stage1_background_sim
)
target_link_libraries(simulate_stage1_background PRIVATE ${FFTW3F_TARGET})
configure_non_cuda_tool(inject_cooperative_target)
target_include_directories(inject_cooperative_target PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/target_injection
  ${CMAKE_SOURCE_DIR}/simulator/common
)
configure_non_cuda_tool(simulate_stage2_statistical)
target_include_directories(simulate_stage2_statistical PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/stage2_statistical_sim
  ${CMAKE_SOURCE_DIR}/simulator/target_injection
  ${CMAKE_SOURCE_DIR}/simulator/common
)
target_link_libraries(simulate_stage2_statistical PRIVATE ${FFTW3_TARGET})
configure_non_cuda_tool(dump_newprotocol_header)
configure_gmti_target(simulate_stage3_sar_scene)
target_include_directories(simulate_stage3_sar_scene PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/stage3_sar_scene_sim
  ${CMAKE_SOURCE_DIR}/simulator/common
)
target_link_libraries(simulate_stage3_sar_scene PRIVATE TIFF::TIFF)
configure_non_cuda_tool(track_assignment_selftest)
configure_non_cuda_tool(low_radial_velocity_filter_selftest)
configure_non_cuda_tool(track_detection_eval)
configure_non_cuda_tool(channel_calibration_selftest)
configure_non_cuda_tool(range_compression_window_selftest)
configure_non_cuda_tool(motion_comp_ctdr_selftest)
configure_non_cuda_tool(velocity_ambiguity_selftest)
configure_non_cuda_tool(velocity_ambiguity_sweep)
configure_non_cuda_tool(p38_phase_fit_selftest)
configure_non_cuda_tool(imu_interpolation_selftest)
configure_non_cuda_tool(trig_lut_selftest)
configure_non_cuda_tool(stage3_scene_mapping_selftest)
target_include_directories(stage3_scene_mapping_selftest PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/stage3_sar_scene_sim
)
configure_non_cuda_tool(beam_visibility_selftest)
target_include_directories(beam_visibility_selftest PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/target_injection
  ${CMAKE_SOURCE_DIR}/simulator/common
)
configure_non_cuda_tool(truth_writer_midpoint_selftest)
target_include_directories(truth_writer_midpoint_selftest PRIVATE
  ${CMAKE_SOURCE_DIR}/simulator/target_injection
  ${CMAKE_SOURCE_DIR}/simulator/common
)
configure_non_cuda_tool(new_protocol_layout_selftest)
configure_non_cuda_tool(mechanical_scan_selftest)
configure_non_cuda_tool(mechanical_phase_center_selftest)
configure_non_cuda_tool(mechanical_protocol_reader_selftest)
configure_non_cuda_tool(shm_echo_input_selftest)
target_include_directories(shm_echo_input_selftest PRIVATE
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
)
if(NOT USE_COREX)
  configure_non_cuda_tool(csi_gate_cpu_selftest)
  target_link_libraries(csi_gate_cpu_selftest PRIVATE
    CUDA::cudart
    CUDA::cufft
  )
endif()
configure_non_cuda_tool(shm_echo_producer)
target_include_directories(shm_echo_producer PRIVATE
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
)
configure_non_cuda_tool(gmti_pipe_test_client)
configure_non_cuda_tool(shm_echo_capture)
target_include_directories(shm_echo_capture PRIVATE
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
)
configure_non_cuda_tool(shmdemo_sender)
target_include_directories(shmdemo_sender PRIVATE
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
)
configure_non_cuda_tool(shmdemo_receiver)
target_include_directories(shmdemo_receiver PRIVATE
  ${CMAKE_SOURCE_DIR}/shmdemo/demo
)

if(BUILD_TESTING)
  add_test(NAME track_assignment_selftest COMMAND track_assignment_selftest)
  add_test(NAME low_radial_velocity_filter_selftest COMMAND low_radial_velocity_filter_selftest)
  add_test(NAME range_compression_window_selftest COMMAND range_compression_window_selftest)
  add_test(NAME motion_comp_ctdr_selftest COMMAND motion_comp_ctdr_selftest)
  add_test(NAME velocity_ambiguity_selftest COMMAND velocity_ambiguity_selftest)
  add_test(NAME channel_calibration_selftest COMMAND channel_calibration_selftest)
  add_test(NAME p38_phase_fit_selftest COMMAND p38_phase_fit_selftest)
  add_test(NAME imu_interpolation_selftest COMMAND imu_interpolation_selftest)
  add_test(NAME trig_lut_selftest COMMAND trig_lut_selftest)
  add_test(NAME stage3_scene_mapping_selftest COMMAND stage3_scene_mapping_selftest)
  add_test(NAME beam_visibility_selftest COMMAND beam_visibility_selftest)
  add_test(NAME truth_writer_midpoint_selftest COMMAND truth_writer_midpoint_selftest)
  add_test(NAME new_protocol_layout_selftest COMMAND new_protocol_layout_selftest)
  add_test(NAME mechanical_scan_selftest COMMAND mechanical_scan_selftest)
  add_test(NAME mechanical_phase_center_selftest COMMAND mechanical_phase_center_selftest)
  add_test(NAME shm_echo_input_selftest COMMAND shm_echo_input_selftest)
  if(NOT USE_COREX)
    add_test(NAME csi_gate_cpu_selftest COMMAND csi_gate_cpu_selftest)
  endif()
endif()
