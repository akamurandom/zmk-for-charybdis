#!/usr/bin/env python3
"""Exercise the patched ZMK functions on a host, without keyboard hardware."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|int|void) " + re.escape(name) + r"\([^;]*?\) \{", source, re.M)
    if not match:
        raise ValueError(f"Function not found: {name}")
    start = source.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


PREAMBLE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_RGB_UNDERGLOW_EXT_POWER 1
#define CONFIG_ZMK_SLEEP 1
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define MAX_IDLE_MS 30000
#define MAX_SLEEP_MS 900000
#define LOG_ERR(...) ((void)0)
enum zmk_activity_state { ZMK_ACTIVITY_ACTIVE, ZMK_ACTIVITY_IDLE, ZMK_ACTIVITY_SLEEP };
struct k_work { int unused; };
struct k_timer { int unused; };
struct device { int unused; };
static struct { bool on; int animation_step; } state;
static bool rgb_suspended, vbus, rail_on, animation_on;
static int save_count, output_count, sleep_count;
static int32_t now;
static uint32_t activity_last_uptime;
static enum zmk_activity_state activity_state;
static struct k_work underglow_tick_work, underglow_off_work;
static struct k_timer underglow_tick;
static struct device strip_device, power_device;
static const struct device *led_strip = &strip_device, *ext_power = &power_device;
static bool charybdis_vbus_is_powered(void) { return vbus; }
static enum zmk_activity_state zmk_activity_get_state(void) { return activity_state; }
static int zmk_rgb_underglow_save_state(void) { save_count++; return 0; }
static int ext_power_enable(const struct device *dev) { rail_on = true; return 0; }
static int ext_power_disable(const struct device *dev) { rail_on = false; return 0; }
static void k_timer_stop(struct k_timer *timer) { animation_on = false; output_count++; }
static void k_timer_start(struct k_timer *timer, int delay, int period) { animation_on = true; }
static void k_work_cancel(struct k_work *work) {}
static void *zmk_workqueue_lowprio_work_q(void) { return NULL; }
static void k_work_submit_to_queue(void *queue, struct k_work *work) {}
static int32_t k_uptime_get(void) { return now; }
static int zmk_pm_suspend_devices(void) { return 0; }
static void zmk_pm_resume_devices(void) {}
static void sys_poweroff(void) { sleep_count++; }
static int charybdis_rgb_update_power_state(void);
static int set_state(enum zmk_activity_state next) {
    if (activity_state == next) return 0;
    activity_state = next;
    return charybdis_rgb_update_power_state();
}
"""

TESTS = r"""
static void check_output(bool expected) {
    assert(rail_on == expected);
    assert(animation_on == expected);
}
int main(void) {
    // User on: USB idle and long idle never extinguish or sleep.
    vbus = true;
    activity_state = ZMK_ACTIVITY_ACTIVE;
    assert(zmk_rgb_underglow_on() == 0);
    check_output(true);
    int initial_saves = save_count;
    now = 31000;
    activity_work_handler(NULL);
    assert(activity_state == ZMK_ACTIVITY_IDLE);
    check_output(true);
    now = 901000;
    activity_work_handler(NULL);
    assert(sleep_count == 0);
    check_output(true);

    // Removal during IDLE must be caught even without an activity event.
    vbus = false;
    now = 32000;
    activity_work_handler(NULL);
    check_output(false);
    assert(state.on && save_count == initial_saves);
    bool requested;
    zmk_rgb_underglow_get_state(&requested);
    assert(requested);
    int previous_output_count = output_count;
    activity_work_handler(NULL);
    assert(output_count == previous_output_count); // stable state is a no-op

    // Power restored while still idle resumes without writing settings.
    vbus = true;
    activity_work_handler(NULL);
    check_output(true);
    assert(save_count == initial_saves);

    // On battery, input wakes RGB; idle suspends it again, without saving.
    vbus = false;
    set_state(ZMK_ACTIVITY_ACTIVE);
    activity_last_uptime = now;
    check_output(true);
    now += 29000;
    activity_work_handler(NULL);
    check_output(true);
    now += 2000;
    activity_work_handler(NULL);
    check_output(false);
    set_state(ZMK_ACTIVITY_ACTIVE);
    check_output(true);
    assert(save_count == initial_saves);

    // Manual off survives idle, wake and USB transitions.
    zmk_rgb_underglow_off();
    assert(!state.on);
    initial_saves = save_count;
    set_state(ZMK_ACTIVITY_IDLE);
    vbus = true;
    charybdis_rgb_update_power_state();
    set_state(ZMK_ACTIVITY_ACTIVE);
    check_output(false);
    assert(save_count == initial_saves);

    // Manual on during battery idle saves the preference but stays dark.
    vbus = false;
    set_state(ZMK_ACTIVITY_IDLE);
    zmk_rgb_underglow_on();
    assert(state.on);
    check_output(false);
    initial_saves = save_count;
    vbus = true;
    charybdis_rgb_update_power_state();
    check_output(true);
    assert(save_count == initial_saves);

    // The same VBUS helper controls deep sleep on either half.
    assert(is_usb_power_present());
    now = 1000000;
    activity_last_uptime = 0;
    activity_work_handler(NULL);
    assert(sleep_count == 0);
    vbus = false;
    assert(!is_usb_power_present());
    activity_work_handler(NULL);
    assert(sleep_count == 1 && activity_state == ZMK_ACTIVITY_SLEEP);
    check_output(false);
    assert(state.on && save_count == initial_saves);
    puts("VBUS RGB/sleep policy: all transition checks passed");
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zmk-source", required=True, type=Path, help="ZMK app/src directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="charybdis-vbus-test-") as tmp:
        temp = Path(tmp)
        for name in ("activity.c", "rgb_underglow.c"):
            shutil.copyfile(args.zmk_source / name, temp / name)
        subprocess.run(["git", "apply", "--no-index",
                        str(root / "module/patches/zmk-v0.3-vbus.patch")], cwd=tmp, check=True)
        rgb = (temp / "rgb_underglow.c").read_text()
        activity = (temp / "activity.c").read_text()
        functions = [function(rgb, name) for name in (
            "rgb_is_lit", "rgb_update_output", "charybdis_rgb_update_power_state",
            "zmk_rgb_underglow_get_state", "zmk_rgb_underglow_on", "zmk_rgb_underglow_off")]
        functions += [function(activity, name) for name in ("is_usb_power_present", "activity_work_handler")]
        # The harness forward declaration must have the production linkage.
        preamble = PREAMBLE.replace("static int charybdis_rgb_update_power_state(void);",
                                    "int charybdis_rgb_update_power_state(void);")
        source = temp / "policy_test.c"
        source.write_text(preamble + "\n".join(functions) + TESTS)
        executable = temp / "policy_test"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        str(source), "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
