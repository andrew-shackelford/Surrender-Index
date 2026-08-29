import json
import os
from pathlib import Path
import pickle
import stat
import tempfile
import unittest
from unittest import mock

import surrender_index_bot as bot


class ConfigurationTests(unittest.TestCase):
    def test_credentials_can_be_loaded_from_external_config_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            expected = {"consumer_key": "placeholder"}
            with (config_directory / "credentials.json").open("w") as file:
                json.dump(expected, file)

            with mock.patch.dict(
                os.environ,
                {bot.CONFIG_DIRECTORY_ENV: str(config_directory)},
            ):
                self.assertEqual(bot.load_credentials(), expected)

    def test_gmail_token_is_written_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            token_value = {"token": "placeholder"}
            token_path = config_directory / "gmail_token.pickle"
            token_path.write_bytes(b"old token")
            token_path.chmod(0o644)

            with mock.patch.dict(
                os.environ,
                {bot.CONFIG_DIRECTORY_ENV: str(config_directory)},
            ):
                bot.write_gmail_token(token_value)

            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
            with token_path.open("rb") as token_file:
                self.assertEqual(pickle.load(token_file), token_value)


if __name__ == "__main__":
    unittest.main()
