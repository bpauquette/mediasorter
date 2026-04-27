import os
import unittest
from unittest.mock import patch

import mediasorter_license as license_mod


class LicenseFlowTests(unittest.TestCase):
    def test_license_checks_are_disabled_without_configured_api(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            license_mod, "_read_runtime_text_file", return_value=""
        ):
            result = license_mod.validate_license_state()

        self.assertTrue(result.valid)
        self.assertFalse(license_mod.license_api_enabled())

    def test_license_checks_enable_when_api_url_is_configured(self):
        with patch.dict(os.environ, {"MEDIASORTER_LICENSE_API_URL": "https://licenses.example.com"}, clear=True):
            self.assertTrue(license_mod.license_api_enabled())


if __name__ == "__main__":
    unittest.main()
