import unittest

from llm_setup_catalog import hardware_options, model_profiles_for_hardware


class LLMSetupCatalogTests(unittest.TestCase):
    def test_hardware_options_include_cpu_and_gpu(self):
        options = hardware_options()
        keys = {item["key"] for item in options}
        self.assertIn("cpu", keys)
        self.assertIn("gpu", keys)

    def test_cpu_profiles_present(self):
        profiles = model_profiles_for_hardware("cpu")
        self.assertGreaterEqual(len(profiles), 2)
        labels = [profile.label for profile in profiles]
        self.assertIn("Small/Fast (Recommended)", labels)
        self.assertIn("Better Accuracy", labels)

    def test_gpu_profiles_present(self):
        profiles = model_profiles_for_hardware("gpu")
        self.assertGreaterEqual(len(profiles), 2)
        models = [profile.ollama_model for profile in profiles]
        self.assertIn("qwen2.5:3b-instruct", models)


if __name__ == "__main__":
    unittest.main()
