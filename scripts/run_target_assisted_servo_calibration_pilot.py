#!/usr/bin/env python3
"""Canonical entry point for the target-assisted servo calibration pilot."""

from run_unknown_system_error_servo_pilot import (
    main,
    run_pilot,
    servo_estimator_contract,
    servo_manifest_contract,
)


if __name__ == "__main__":
    raise SystemExit(main())
