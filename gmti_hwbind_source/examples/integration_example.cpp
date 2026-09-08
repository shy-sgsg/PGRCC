#include "gmti_hwbind/security_gate.h"

#include <iostream>

namespace {

void initialization() {}
void algorithm_stage_1() {}
void algorithm_stage_2() {}
void algorithm_stage_3() {}
void algorithm_stage_4() {}
void algorithm_stage_5() {}
void algorithm_stage_6() {}

} // namespace

int main() {
    using gmti::security::CheckpointId;

    gmti::security::reset_gate();

    gmti::security::checkpoint(CheckpointId::Initialization);
    initialization();

    gmti::security::checkpoint(CheckpointId::Check1);
    algorithm_stage_1();

    gmti::security::checkpoint(CheckpointId::Check2);
    algorithm_stage_2();

    gmti::security::checkpoint(CheckpointId::Check3);
    algorithm_stage_3();

    gmti::security::checkpoint(CheckpointId::Check4);
    algorithm_stage_4();

    gmti::security::checkpoint(CheckpointId::Check5);
    algorithm_stage_5();

    gmti::security::checkpoint(CheckpointId::Check6);
    algorithm_stage_6();

    if (!gmti::security::final_decision()) {
        std::cerr << "GMTI authorization failed\n";
        return 1;
    }

    std::cout << "GMTI authorization passed\n";
    return 0;
}

