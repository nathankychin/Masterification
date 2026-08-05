import unittest

from app import get_practice_mode_data


class PracticeModeTests(unittest.TestCase):
    def test_language_mode_uses_speaking_guidance(self):
        data = get_practice_mode_data("Spanish", "Languages")
        self.assertEqual(data["type"], "speech")
        self.assertIn("microphone", data["instruction"].lower())
        self.assertIn("phrase", data["prompt"].lower())

    def test_music_mode_uses_note_or_chord_guidance(self):
        data = get_practice_mode_data("Piano", "Musical")
        self.assertEqual(data["type"], "music")
        self.assertIn("chord", data["prompt"].lower())
        self.assertIn("microphone", data["instruction"].lower())


if __name__ == "__main__":
    unittest.main()
