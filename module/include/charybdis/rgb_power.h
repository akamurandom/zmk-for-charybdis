/* SPDX-License-Identifier: MIT */
#pragma once

/* Also called by activity.c once a second to catch power-only transitions. */
int charybdis_rgb_update_power_state(void);
