"""Regression tests for the complete-fish confidence threshold policy.

These tests deliberately exercise threshold policy, CLI plumbing, and a mocked
filtering route. They do not construct the ViT model or require its local
checkpoint, Torch, OpenCV, or an input image.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from algorithms.core import filter_complete_fish
from algorithms.tools import run_video_pipeline


class CompleteThresholdTests(unittest.TestCase):
    def test_threshold_boundary_is_inclusive(self) -> None:
        """Only scores at or above 0.9 are classified as complete."""

        self.assertFalse(filter_complete_fish.is_complete_score(0.899999))
        self.assertTrue(filter_complete_fish.is_complete_score(0.9))
        self.assertTrue(filter_complete_fish.is_complete_score(0.900001))
        self.assertTrue(filter_complete_fish.is_complete_score(0.95))

    def test_invalid_score_and_threshold_values_are_rejected(self) -> None:
        """Probability inputs must be finite values in the closed unit interval."""

        invalid_values = (
            -0.1,
            1.1,
            math.nan,
            math.inf,
            -math.inf,
            "not-a-number",
            None,
        )
        for value in invalid_values:
            with self.subTest(score=value):
                with self.assertRaises(ValueError):
                    filter_complete_fish.is_complete_score(value)

            with self.subTest(threshold=value):
                with self.assertRaises(ValueError):
                    filter_complete_fish.is_complete_score(0.9, value)

    def test_complete_argmax_below_threshold_is_reclassified_incomplete(self) -> None:
        """The business threshold overrides an otherwise complete raw argmax."""

        raw_result = SimpleNamespace(
            label="complete",
            code=0,
            confidence=0.89,
            scores={"complete": 0.89, "incomplete": 0.11},
            is_complete=True,
        )

        is_complete, label, complete_score = (
            filter_complete_fish.classify_completeness_result(raw_result)
        )

        self.assertEqual(raw_result.label, "complete")
        self.assertTrue(raw_result.is_complete)
        self.assertFalse(is_complete)
        self.assertEqual(label, "incomplete")
        self.assertEqual(complete_score, 0.89)

    def test_process_directory_routes_low_complete_argmax_to_incomplete(self) -> None:
        """A raw complete argmax at 0.89 must use the incomplete save path."""

        raw_result = SimpleNamespace(
            label="complete",
            code=0,
            confidence=0.89,
            scores={"complete": 0.89, "incomplete": 0.11},
            is_complete=True,
        )
        classifier = MagicMock()
        classifier.predict_path.return_value = raw_result
        get_default_classifier = MagicMock(return_value=classifier)
        fake_cv2 = SimpleNamespace(imwrite=MagicMock(return_value=True))

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_dir = temp_dir / "input"
            complete_dir = temp_dir / "complete"
            incomplete_dir = temp_dir / "incomplete"
            input_dir.mkdir()
            crop_path = input_dir / "crop.jpg"
            mask_path = input_dir / "mask.png"
            crop_path.touch()
            mask_path.touch()
            output = StringIO()

            with patch.object(
                filter_complete_fish,
                "_processing_functions",
                return_value={"get_default_classifier": get_default_classifier},
            ), patch.object(
                filter_complete_fish,
                "iter_crop_mask_pairs",
                return_value=[("id_7_000000000042", crop_path, mask_path, 7, 42)],
            ), patch.object(
                filter_complete_fish,
                "read_crop_mask_images",
                return_value=(object(), object()),
            ), patch.object(
                filter_complete_fish,
                "make_black_background_fish_from_images",
                return_value=object(),
            ), patch.object(
                filter_complete_fish,
                "_cv2",
                return_value=fake_cv2,
            ), patch.object(
                filter_complete_fish,
                "align_complete_pair",
            ) as align_complete_pair, patch.object(
                filter_complete_fish,
                "save_aligned_pair",
            ) as save_aligned_pair, patch.object(
                filter_complete_fish,
                "save_incomplete_pair",
            ) as save_incomplete_pair:
                with redirect_stdout(output):
                    result = filter_complete_fish.process_directory(
                        input_dir,
                        complete_dir,
                        incomplete_dir,
                        complete_threshold=0.9,
                    )

        self.assertEqual(result, 0)
        self.assertEqual(raw_result.label, "complete")
        self.assertTrue(raw_result.is_complete)
        self.assertEqual(raw_result.scores["complete"], 0.89)
        align_complete_pair.assert_not_called()
        save_aligned_pair.assert_not_called()
        save_incomplete_pair.assert_called_once_with(
            crop_path,
            mask_path,
            7,
            42,
            incomplete_dir,
        )
        self.assertIn("model_label=complete", output.getvalue())
        self.assertIn("complete_score=0.890000", output.getvalue())
        self.assertIn("threshold=0.900000", output.getvalue())


class CompleteThresholdCliTests(unittest.TestCase):
    def test_filter_cli_defaults_to_point_nine(self) -> None:
        """The standalone filter CLI still defaults to the shared default."""

        filter_args = filter_complete_fish.parse_args(["--video-id", "video-test"])

        self.assertEqual(
            filter_args.complete_threshold,
            filter_complete_fish.DEFAULT_COMPLETE_THRESHOLD,
        )
        self.assertEqual(filter_args.complete_threshold, 0.9)

    def test_pipeline_arg_defaults_to_none_for_config_resolution(self) -> None:
        """The top-level pipeline leaves the threshold unset so the admin config wins."""

        pipeline_args = run_video_pipeline.parse_args(["--input-video", "input.mp4"])

        self.assertIsNone(pipeline_args.complete_threshold)

    def test_pipeline_resolves_configured_threshold(self) -> None:
        """When the admin config sets complete_threshold, that value is used."""

        with tempfile.TemporaryDirectory() as temp_dir_name:
            config_path = Path(temp_dir_name) / "adjust_calibration_margin.json"
            config_path.write_text(
                '{"side_margin_ratio": 0.13, "complete_threshold": 0.88}',
                encoding="utf-8",
            )
            with patch.object(
                run_video_pipeline,
                "ADJUST_CALIBRATION_MARGIN_CONFIG_PATH",
                config_path,
            ):
                self.assertEqual(run_video_pipeline.resolve_complete_threshold(None), 0.88)

    def test_pipeline_resolves_fallback_default_when_config_missing(self) -> None:
        """Without a config file the pipeline falls back to the default threshold."""

        with tempfile.TemporaryDirectory() as temp_dir_name:
            missing_path = Path(temp_dir_name) / "does_not_exist.json"
            with patch.object(
                run_video_pipeline,
                "ADJUST_CALIBRATION_MARGIN_CONFIG_PATH",
                missing_path,
            ):
                self.assertEqual(
                    run_video_pipeline.resolve_complete_threshold(None),
                    filter_complete_fish.DEFAULT_COMPLETE_THRESHOLD,
                )

    def test_pipeline_explicit_threshold_overrides_config(self) -> None:
        """An explicit CLI value wins over the admin-configured value."""

        with tempfile.TemporaryDirectory() as temp_dir_name:
            config_path = Path(temp_dir_name) / "adjust_calibration_margin.json"
            config_path.write_text(
                '{"complete_threshold": 0.88}',
                encoding="utf-8",
            )
            with patch.object(
                run_video_pipeline,
                "ADJUST_CALIBRATION_MARGIN_CONFIG_PATH",
                config_path,
            ):
                self.assertEqual(run_video_pipeline.resolve_complete_threshold(0.93), 0.93)

    def test_pipeline_step_forwards_explicit_threshold(self) -> None:
        """An explicitly requested threshold reaches the filter submodule unchanged."""

        args = argparse.Namespace(
            video_id="video-test",
            complete_threshold=0.93,
            force_all_complete=False,
            simulate_failure_stage="none",
        )
        with patch.object(
            run_video_pipeline,
            "_call_submodule_main",
            return_value=0,
        ) as call_submodule, patch.object(
            run_video_pipeline,
            "maybe_simulate_failure",
        ):
            result = run_video_pipeline.step_filter_complete(args, {})

        self.assertEqual(result, 0)
        call_submodule.assert_called_once_with(
            "algorithms.core.filter_complete_fish",
            [
                "--video-id",
                "video-test",
                "--complete-threshold",
                "0.93",
            ],
        )


if __name__ == "__main__":
    unittest.main()
