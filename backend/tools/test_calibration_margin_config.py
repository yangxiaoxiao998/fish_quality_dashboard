"""完整性阈值（complete_threshold）配置服务的回归测试。

不依赖前端与数据库；直接调用 backend service，并把 margin 配置文件
临时重定向到独立目录，验证管理员在前端输入的完整性阈值能够：
  1. 正确保存到 adjust_calibration_margin.json；
  2. 被 get_margin_config 读取回来；
  3. 非法值（超出 [0, 1]）被拒绝。

Usage:
    /home/dsl/miniconda3/envs/full/bin/python backend/tools/test_calibration_margin_config.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import calibration_config_service as service  # noqa: E402


class CompleteThresholdMarginConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.margin_file = Path(self._temp.name) / "adjust_calibration_margin.json"

    def test_save_and_load_complete_threshold(self) -> None:
        with patch.object(service, "PROJECT_MARGIN_FILE", self.margin_file):
            saved = service.save_margin_config({"complete_threshold": 0.77})
            self.assertEqual(saved["complete_threshold"], 0.77)

            raw = json.loads(self.margin_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["complete_threshold"], 0.77)

            loaded = service.get_margin_config()
            self.assertEqual(loaded["complete_threshold"], 0.77)

    def test_complete_threshold_defaults_to_point_nine(self) -> None:
        with patch.object(service, "PROJECT_MARGIN_FILE", self.margin_file):
            loaded = service.get_margin_config()
            self.assertEqual(loaded["complete_threshold"], 0.9)

    def test_rejects_out_of_range_complete_threshold(self) -> None:
        with patch.object(service, "PROJECT_MARGIN_FILE", self.margin_file):
            for bad in (1.1, -0.01, 2.0):
                with self.subTest(value=bad):
                    with self.assertRaises(ValueError):
                        service.save_margin_config({"complete_threshold": bad})

    def test_accepts_boundary_values(self) -> None:
        with patch.object(service, "PROJECT_MARGIN_FILE", self.margin_file):
            self.assertEqual(
                service.save_margin_config({"complete_threshold": 0.0})["complete_threshold"],
                0.0,
            )
            self.assertEqual(
                service.save_margin_config({"complete_threshold": 1.0})["complete_threshold"],
                1.0,
            )

    def test_preserves_other_margin_fields_on_partial_update(self) -> None:
        with patch.object(service, "PROJECT_MARGIN_FILE", self.margin_file):
            service.save_margin_config({"complete_threshold": 0.66})
            saved = service.save_margin_config({"complete_threshold": 0.55})
            self.assertEqual(saved["complete_threshold"], 0.55)
            self.assertEqual(saved["side_margin_ratio"], 0.13)
            self.assertEqual(saved["conveyor_direction"], "top_to_bottom")


if __name__ == "__main__":
    unittest.main()
