"""Optional real-X11 smoke tests, always on a private Xvfb display and temporary home."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_desktop import display, ROOT

XVFB = os.environ.get("DOSHY_TEST_XVFB") or shutil.which("Xvfb")


@unittest.skipUnless(XVFB and shutil.which("openbox") and shutil.which("zenity"), "Xvfb/Openbox/Zenity required")
class X11SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.home = Path(cls.temp.name)
        read_fd, write_fd = os.pipe()
        cls.server = subprocess.Popen([XVFB, "-displayfd", str(write_fd), "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-noreset"],
                                      pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        cls.addClassCleanup(cls.stop_server)
        with os.fdopen(read_fd) as pipe:
            if not select.select([pipe], [], [], 10)[0]:
                raise RuntimeError("The private test display did not start")
            number = pipe.readline().strip()
        if not number.isdigit():
            raise RuntimeError("The private test display returned an invalid address")
        cls.environment = patch.dict(os.environ, {"DISPLAY": ":" + number, "HOME": str(cls.home),
                                                "XDG_CONFIG_HOME": str(cls.home / ".config")})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        cls.state_patch = patch.object(display, "STATE", cls.home / ".config/doshy/displays.json")
        cls.state_patch.start()
        cls.addClassCleanup(cls.state_patch.stop)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)

    def test_actual_randr_apply_save_and_restore(self):
        outputs = display.query()
        self.assertEqual(len(outputs), 1)
        outputs[0]["primary"] = True
        display.apply(display.snapshot(outputs))
        display.save(display.query())
        display.restore()
        self.assertTrue(display.query()[0]["primary"])

    def test_actual_zenity_confirmation_times_out(self):
        result = display.dialog("question", "Automated isolated display check", "--timeout=1")
        self.assertEqual(result.returncode, 5, result.stderr)

    def test_openbox_accepts_generated_configuration(self):
        source = (ROOT / "config/includes.chroot/etc/xdg/openbox/rc.xml").read_text()
        config = self.home / "rc.xml"
        config.write_text(display.openbox_config(source, "1"))
        process = subprocess.Popen(["openbox", "--config-file", str(config)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            result = display.dialog("info", "Openbox configuration check", "--timeout=1")
            self.assertIn(result.returncode, (0, 5), result.stderr)
            self.assertIsNone(process.poll(), "Openbox exited with the new configuration")
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
        self.assertNotIn("XML", stderr)


if __name__ == "__main__":
    unittest.main()
