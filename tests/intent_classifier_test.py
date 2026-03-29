import unittest

from glados.system.intent_classifier import IntentClassifier


class TestIntentClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = IntentClassifier.get_instance()
        self.classifier.intents = []  # Reset intents for each test
        self.classifier.model = None

    def test_predict_intent(self):
        self.classifier.add_intents([
            {"name": "addition", "examples": ["Add 5 and 7", "What is 10 plus 3?", "Calculate 2 + 2."]},
            {"name": "subtraction", "examples": ["Subtract 5 from 10", "What is 15 minus 5?", "Calculate 20 - 7."]}
        ])
        self.classifier.retrain()
        intent, confidence = self.classifier.predict_intent("What is 10 minus 3?")
        self.assertEqual(intent, "subtraction")
        self.assertGreater(confidence, 0.5)

    def test_predict_intent_no_training_data(self):
        with self.assertRaises(ValueError):
            self.classifier.predict_intent("Test query")

    # def test_evaluate(self):
    #     self.classifier.add_intents([
    #         {"name": "addition", "examples": ["Add 5 and 7", "What is 10 plus 3?", "Calculate 2 + 2.", "Sum 3 and 9."]},
    #         {"name": "subtraction", "examples": ["Subtract 5 from 10", "What is 15 minus 5?", "Calculate 20 - 7.",
    #                                              "Difference between 8 and 3."]},
    #     ])
    #     metrics = self.classifier.evaluate(test_size=0.2)
    #     self.assertGreater(metrics["accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
