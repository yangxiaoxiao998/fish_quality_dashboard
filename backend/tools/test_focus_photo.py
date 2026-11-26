"""Check focus capture URLs and both Pi focus API response paths."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.paths import DATA_DIR
from app.routers import camera_yzt_router
from app.services.camera_yzt.camera_yzt_service import CameraServiceYzt


class FocusPhotoTest(unittest.TestCase):
    def test_capture_and_confirm_images_are_served(self):
        encoded, frame = cv2.imencode(".jpg", np.zeros((24, 32, 3), dtype=np.uint8))
        self.assertTrue(encoded)
        jpeg = frame.tobytes()
        service = CameraServiceYzt()
        self.assertEqual(service.focus_photo_dir, DATA_DIR / "calibration" / "focus")
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            with (
                patch("app.paths.DATA_DIR", data),
                patch("app.services.path_service.DATA_DIR", data),
                patch("app.services.camera_yzt.camera_yzt_service.DATA_DIR", data),
                patch.object(camera_yzt_router, "camera_service_yzt", service),
                patch.object(service, "get_latest_frame", return_value=jpeg),
                patch.object(service, "focus_update") as update,
                patch.object(service, "stop_stream") as stop,
                patch.object(service, "start_stream") as start,
                patch.object(service, "get_status", return_value={"stream_ready": True}),
            ):
                # No lifespan: this regression test must not start device/DB services.
                client = TestClient(create_app())
                response = client.post("/api/camera_yzt/focus/photo")
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_photo(client, response.json(), "original", jpeg)
                for result in ({"ok": True}, {"ok": False, "status_code": 404}):
                    update.return_value = result
                    response = client.post(
                        "/api/camera_yzt/focus/confirm",
                        json={"autofocus_mode": "manual", "lens_position": 2.0},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assert_photo(client, response.json(), "focused", jpeg)
                stop.assert_called_once()
                start.assert_called_once()
                self.assertFalse(start.call_args.kwargs["write_video"])
                self.assertFalse(start.call_args.kwargs["create_video_task"])

    def assert_photo(self, client, payload, name, jpeg):
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["photo_url"], f"/static/calibration/focus/{name}.jpg")
        image = client.get(payload["photo_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertEqual(image.content, jpeg)


if __name__ == "__main__":
    unittest.main()
