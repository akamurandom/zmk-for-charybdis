/* SPDX-License-Identifier: MIT */
#pragma once

#include <stdbool.h>
#include <hal/nrf_power.h>

/* Read-only: do not take over POWER/CLOCK interrupts from Zephyr or USB. */
static inline bool charybdis_vbus_is_powered(void) {
    return nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
}
