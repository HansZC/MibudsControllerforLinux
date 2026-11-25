from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mibuds_probe.audio import get_audio_info


class AudioTests(unittest.TestCase):
    def test_bluetooth_sink_codec_and_format(self) -> None:
        payload = json.dumps([
            {
                "id": 112,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {
                        "media.class": "Audio/Sink",
                        "api.bluez5.address": "24:B2:31:96:68:50",
                        "api.bluez5.codec": "aac",
                        "api.bluez5.profile": "a2dp-sink",
                        "node.name": "bluez_output.test",
                        "node.description": "REDMI Buds 8 Pro",
                    },
                    "params": {
                        "EnumFormat": [{"format": "S16LE", "rate": 48000, "channels": 2}],
                    },
                },
            }
        ])
        with patch("mibuds_probe.audio.subprocess.check_output", return_value=payload):
            info = get_audio_info("24:B2:31:96:68:50")
        self.assertIsNotNone(info)
        self.assertEqual(info.codec, "AAC")
        self.assertEqual(info.profile, "a2dp-sink")
        self.assertEqual(info.sample_rate, 48000)
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.format, "S16LE")

    def test_missing_sink_returns_none(self) -> None:
        with patch("mibuds_probe.audio.subprocess.check_output", return_value="[]"):
            self.assertIsNone(get_audio_info("00:00:00:00:00:00"))


if __name__ == "__main__":
    unittest.main()
