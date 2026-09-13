"""Display/driver tests use temporary homes and mocked hardware, never the host display/APT."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "config/includes.chroot/usr/local/lib/doshy"


def load(name):
    spec = importlib.util.spec_from_file_location(name, LIB / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


display, drivers = load("display"), load("drivers")
RANDR = """Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767
HDMI-1 connected 1920x1080+0+0 (normal left inverted right x axis y axis)
    EDID:
        00ffffffffffff001234567812345678
   1920x1080 60.00*+ 59.94
   1280x720 60.00
DP-1 connected primary 2560x1440+1920+0 (normal left inverted right x axis y axis)
    EDID:
        00ffffffffffff008765432187654321
   2560x1440 144.00*+ 60.00
   1280x720 60.00
DP-2 disconnected (normal left inverted right x axis y axis)
"""


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.outputs = display.parse_outputs(RANDR)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "displays.json"
        self.state_patch = patch.object(display, "STATE", self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_parses_primary_rate_edid_and_rotation(self):
        self.assertEqual(len(self.outputs), 2)
        self.assertTrue(self.outputs[1]["primary"])
        self.assertEqual(self.outputs[1]["rate"], "144.00")
        self.assertEqual(self.outputs[1]["rotation"], "normal")
        self.assertEqual(self.outputs[0]["modes"]["1920x1080"], ["60.00", "59.94"])
        rotated = display.parse_outputs(RANDR.replace("+1920+0 (normal", "+1920+0 left (normal"))
        self.assertEqual(rotated[1]["rotation"], "left")

    def test_layout_round_trip_and_distinct_monitor_profiles(self):
        display.save(self.outputs)
        display.save(self.outputs[:1])
        profiles = json.loads(self.state.read_text())["profiles"]
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[display.profile_key(self.outputs)], display.snapshot(self.outputs))
        different = copy.deepcopy(self.outputs)
        different[0]["edid"] = "a replacement monitor"
        self.assertNotIn(display.profile_key(different), profiles)
        self.assertEqual(display.profile_key(self.outputs), display.profile_key(self.outputs[::-1]))

    def test_command_restores_rate_positions_rotation_and_disabled_screen(self):
        layout = display.snapshot(self.outputs)
        layout[0].update(mode=None, primary=False)
        layout[1]["rotation"] = "left"
        command = display.command(layout, self.outputs)
        self.assertEqual(command[1:4], ["--output", "HDMI-1", "--off"])
        self.assertIn("144.00", command)
        self.assertIn("left", command)
        self.assertIn("0x0", command)
        self.assertEqual(command.count("--primary"), 1)

    def test_invalid_layouts_fail_before_randr(self):
        for mutation in (lambda l: l[0].update(mode="8000x8000"),
                         lambda l: l[1].update(rate="999"),
                         lambda l: l[0].update(rotation="--off"),
                         lambda l: l[0].update(name="bad"),
                         lambda l: [o.update(mode=None) for o in l]):
            layout = display.snapshot(self.outputs)
            mutation(layout)
            with self.assertRaises(ValueError):
                display.command(layout, self.outputs)

    def test_cancel_and_timeout_restore_without_saving(self):
        for status in (1, 5):
            before = display.snapshot(self.outputs)
            changed = copy.deepcopy(before)
            changed[0]["primary"], changed[1]["primary"] = True, False
            with patch.object(display, "apply") as apply, patch.object(display, "integrate"), \
                    patch.object(display, "dialog", return_value=subprocess.CompletedProcess([], status)), \
                    patch.object(display, "save") as save:
                display.transaction(changed, before)
                self.assertEqual(apply.call_args_list[0].args[0], changed)
                self.assertEqual(apply.call_args_list[1].args[0], before)
                save.assert_not_called()

    def test_keep_saves_actual_confirmed_state(self):
        before = display.snapshot(self.outputs)
        with patch.object(display, "apply") as apply, patch.object(display, "integrate"), \
                patch.object(display, "dialog", return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(display, "query", return_value=self.outputs):
            display.transaction(before, before)
            self.assertEqual(apply.call_count, 1)
        self.assertEqual(json.loads(self.state.read_text())["profiles"][display.profile_key(self.outputs)], before)

    def test_failed_apply_rolls_back(self):
        layout = display.snapshot(self.outputs)
        with patch.object(display, "apply", side_effect=[ValueError("failure"), None]) as apply, \
                patch.object(display, "integrate"), patch.object(display, "save") as save:
            with self.assertRaisesRegex(ValueError, "failure"):
                display.transaction(layout, layout)
            self.assertEqual(apply.call_count, 2)
            save.assert_not_called()

    def test_restore_after_fresh_session(self):
        display.save(self.outputs)
        with patch.object(display, "query", return_value=self.outputs), patch.object(display, "apply") as apply:
            display.restore()
            apply.assert_called_once_with(display.snapshot(self.outputs))

    def test_missing_monitor_does_not_destroy_saved_profile(self):
        display.save(self.outputs)
        saved = self.state.read_bytes()
        with patch.object(display, "query", return_value=self.outputs[:1]), \
                patch.object(display, "apply") as apply, patch.object(display, "run"):
            display.restore()
            apply.assert_not_called()
        self.assertEqual(self.state.read_bytes(), saved)

    def test_openbox_preserves_shortcuts_and_targets_main_screen(self):
        original = (ROOT / "config/includes.chroot/etc/xdg/openbox/rc.xml").read_text()
        updated = display.openbox_config(original, "2")
        self.assertEqual(updated, display.openbox_config(updated, "2"))
        root = ET.fromstring(updated)
        ns = {"o": display.NS}
        self.assertEqual(root.find("o:placement/o:primaryMonitor", ns).text, "2")
        self.assertEqual(len(root.findall("o:keyboard/o:keybind", ns)),
                         len(ET.fromstring(original).findall("o:keyboard/o:keybind", ns)))
        rule = root.find("o:applications/o:application[@doshy-main-screen='yes']/o:position", ns)
        self.assertEqual(rule.get("force"), "yes")
        self.assertEqual(rule.find("o:monitor", ns).text, "2")
        self.assertEqual(display.main_index(self.outputs, "head #0: 1920x1080 @ 0,0\nhead #1: 2560x1440 @ 1920,0"), "2")

    def test_mirror_uses_common_resolution(self):
        with patch.object(display, "query", return_value=self.outputs), \
                patch.object(display, "choose", return_value="Mirror screens"), \
                patch.object(display, "transaction") as transaction:
            display.gui()
            self.assertEqual([o["mode"] for o in transaction.call_args.args[0]], ["1280x720"] * 2)

    def test_unsupported_mirror_never_changes_screens(self):
        self.outputs[1]["modes"].pop("1280x720")
        with patch.object(display, "query", return_value=self.outputs), \
                patch.object(display, "choose", return_value="Mirror screens"), \
                patch.object(display, "transaction") as transaction:
            with self.assertRaisesRegex(ValueError, "no common"):
                display.gui()
            transaction.assert_not_called()

    def test_swap_preserves_main_monitor_and_uses_native_width(self):
        with patch.object(display, "query", return_value=self.outputs), \
                patch.object(display, "choose", return_value="Swap screens"), \
                patch.object(display, "transaction") as transaction:
            display.gui()
            layout = transaction.call_args.args[0]
            self.assertEqual(layout[0]["x"], 2560)
            self.assertEqual(layout[1]["x"], 0)
            self.assertTrue(layout[1]["primary"])


class DriverTests(unittest.TestCase):
    def device(self, vendor="0x10de"):
        return [{"vendor": vendor, "device": "0x1234", "driver": "nouveau", "slot": "0000:01:00.0", "name": "Fixture GPU"}]

    def test_intel_amd_and_hybrid_recommendations(self):
        intel = drivers.packages_for(self.device("0x8086"), "open", True, False)
        self.assertIn("firmware-misc-nonfree", intel)
        self.assertNotIn("linux-image-amd64", intel)
        amd = drivers.packages_for(self.device("0x1002"), "open", False, False)
        self.assertIn("firmware-amd-graphics", amd)
        self.assertIn("linux-image-amd64", amd)
        hybrid = drivers.packages_for(self.device() + self.device("0x8086"), "nvidia", False, False, "  nvidia-driver\n")
        self.assertIn("nvidia-driver", hybrid)
        self.assertIn("linux-headers-amd64", hybrid)
        self.assertIn("firmware-misc-nonfree", hybrid)

    def test_nvidia_detection_requires_one_known_recommendation(self):
        self.assertEqual(drivers.recommended_nvidia("It is recommended to install the\n nvidia-tesla-470-driver\n package."), "nvidia-tesla-470-driver")
        for text in ("nvidia-evil-driver", "nvidia-driver\nnvidia-tesla-470-driver", "unsupported", "Use nvidia-driver maybe"):
            self.assertIsNone(drivers.recommended_nvidia(text))

    def test_nvidia_blocks_live_secure_boot_unknown_and_unsupported(self):
        for is_live, secure, detection in ((True, False, "nvidia-driver"), (False, True, "nvidia-driver"),
                                           (False, None, "nvidia-driver"), (False, False, "unsupported")):
            with self.assertRaises(ValueError):
                drivers.packages_for(self.device(), "nvidia", is_live, secure, detection)

    def test_driver_simulation_blocks_removal_and_live_boot_changes(self):
        for output in ("Remv openbox [3.6]", "Inst linux-image-6.1.0-amd64 (1 Debian)", "Inst nvidia-kernel-dkms (1 Debian)"):
            with patch.object(drivers, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaises(ValueError):
                    drivers.simulation(["mesa-vulkan-drivers=1"], True)

    def test_offline_error_does_not_create_plan(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(drivers, "STORAGE", Path(folder)), \
                patch.object(drivers, "hardware", return_value=self.device("0x8086")), \
                patch.object(drivers, "live", return_value=True), patch.object(drivers, "secure_boot", return_value=False), \
                patch.object(drivers, "run", side_effect=ValueError("network unavailable")):
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                drivers.prepare("open")
            self.assertFalse((Path(folder) / "plan.json").exists())

    def test_candidate_status_and_missing_packages(self):
        output = "mesa-vulkan-drivers:\n  Installed: 1\n  Candidate: 2\n"
        with patch.object(drivers, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertEqual(drivers.versions(["mesa-vulkan-drivers"])["mesa-vulkan-drivers"], {"installed": "1", "candidate": "2"})
            with self.assertRaisesRegex(ValueError, "No Debian 12 candidate"):
                drivers.versions(["missing"])

    def test_install_token_and_changed_system_checks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(drivers, "STORAGE", Path(folder)), \
                patch.object(drivers, "hardware", return_value=self.device()), \
                patch.object(drivers, "installed_state", return_value="new-state"), \
                patch.object(drivers, "run") as run:
            plan = dict(token="a" * 32, created=time.time(), devices=self.device(), installed="old-state")
            drivers.write_json(Path(folder) / "plan.json", plan)
            for token in ("../../etc/passwd", "b" * 32, "a" * 32):
                with self.assertRaises(ValueError):
                    drivers.install(token)
            run.assert_not_called()

    def test_successful_install_uses_approved_versions_and_keeps_recovery_record(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(drivers, "STORAGE", Path(folder)), \
                patch.object(drivers, "hardware", return_value=self.device()), \
                patch.object(drivers, "installed_state", return_value="state"), \
                patch.object(drivers, "live", return_value=True), patch.object(drivers, "secure_boot", return_value=False), \
                patch.object(drivers, "simulation", return_value=["Inst mesa-vulkan-drivers (2 Debian)"]), \
                patch.object(drivers, "run", return_value=subprocess.CompletedProcess([], 0, "done", "")) as run:
            plan = dict(token="a" * 32, created=time.time(), devices=self.device(), installed="state",
                        specs=["mesa-vulkan-drivers=2"], operations=["Inst mesa-vulkan-drivers (2 Debian)"], live=True, secure_boot=False)
            drivers.write_json(Path(folder) / "plan.json", plan)
            self.assertTrue(drivers.install("a" * 32)["reboot"])
            command = run.call_args.args[0]
            self.assertIn("--no-remove", command)
            self.assertIn("mesa-vulkan-drivers=2", command)
            self.assertFalse((Path(folder) / "plan.json").exists())
            self.assertEqual(json.loads((Path(folder) / "last-transaction.json").read_text())["status"], "complete")

    def test_privileged_helper_rejects_unprivileged_calls(self):
        with patch.object(drivers.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "Administrator"):
                drivers.helper("install", "a" * 32)


if __name__ == "__main__":
    unittest.main()
