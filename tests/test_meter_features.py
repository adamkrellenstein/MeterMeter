import unittest

from metermeter.meter_engine import MeterEngine


class MeterFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MeterEngine()

    def test_iambic_masculine_no_subs(self) -> None:
        feats = self.engine.meter_features_for("iambic pentameter", "USUSUSUSUS")
        self.assertEqual(feats["ending"], "masc")
        self.assertFalse(feats["inversion"])
        self.assertFalse(feats["initial_inversion"])
        self.assertFalse(feats["spondee"])
        self.assertFalse(feats["pyrrhic"])

    def test_iambic_feminine_ending(self) -> None:
        feats = self.engine.meter_features_for("iambic pentameter", "USUSUSUSUSU")
        self.assertEqual(feats["ending"], "fem")

    def test_iambic_initial_inversion(self) -> None:
        feats = self.engine.meter_features_for("iambic pentameter", "SUUSUSUSUS")
        self.assertEqual(feats["ending"], "masc")
        self.assertTrue(feats["inversion"])
        self.assertTrue(feats["initial_inversion"])

    def test_iambic_spondee(self) -> None:
        feats = self.engine.meter_features_for("iambic pentameter", "SSUSUSUSUS")
        self.assertEqual(feats["ending"], "masc")
        self.assertTrue(feats["spondee"])

    def test_iambic_pyrrhic(self) -> None:
        feats = self.engine.meter_features_for("iambic pentameter", "UUUSUSUSUS")
        self.assertEqual(feats["ending"], "masc")
        self.assertTrue(feats["pyrrhic"])

