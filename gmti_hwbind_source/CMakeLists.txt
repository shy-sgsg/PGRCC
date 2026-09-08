cmake_minimum_required(VERSION 3.14)
project(gmti_hwbind_source LANGUAGES CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "gmti_hwbind currently supports Linux only")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)

add_library(gmti_hwbind STATIC
  src/binding.cpp
  src/checkpoint_codec.cpp
  src/hardware_linux.cpp
  src/security_gate.cpp
  src/sm3.cpp
)

target_include_directories(gmti_hwbind
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(gmti_hwbind PUBLIC Threads::Threads)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(gmti_hwbind PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(gmti_target_keygen tools/target_keygen.cpp)
target_link_libraries(gmti_target_keygen PRIVATE gmti_hwbind)

add_executable(gmti_integration_example examples/integration_example.cpp)
target_link_libraries(gmti_integration_example PRIVATE gmti_hwbind)

option(GMTI_HWBIND_BUILD_TESTS "Build GMTI hardware-binding tests" ON)
if(GMTI_HWBIND_BUILD_TESTS)
  enable_testing()
  add_executable(gmti_hwbind_tests tests/test_package.cpp)
  target_include_directories(gmti_hwbind_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
  target_link_libraries(gmti_hwbind_tests PRIVATE gmti_hwbind)
  add_test(NAME gmti_hwbind_tests COMMAND gmti_hwbind_tests)
endif()

