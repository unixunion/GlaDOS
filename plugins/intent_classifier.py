from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline

class IntentClassifier:
    def __init__(self, intents: list[dict]):
        self.intents = intents
        self.model = self._train_model()

    def _train_model(self):
        texts = []
        labels = []

        for intent in self.intents:
            for example in intent["examples"]:
                texts.append(example)
                labels.append(intent["name"])

        pipeline = Pipeline([
            ("vectorizer", CountVectorizer()),
            ("classifier", MultinomialNB())
        ])

        return pipeline.fit(texts, labels)

    def predict_intent(self, text: str) -> (str, float):
        """
        Predicts the intent for a given text, along with the confidence score.

        Args:
            text (str): The input text to classify.

        Returns:
            Tuple[str, float]: The predicted intent and its confidence score.
        """
        probabilities = self.model.predict_proba([text])[0]
        max_index = probabilities.argmax()
        intent = self.model.classes_[max_index]
        confidence = probabilities[max_index]
        return intent, confidence


# class IntentClassifier:
#     def __init__(self, intents: list[dict]):
#         """
#         Initializes the intent classifier with intents data.
#
#         Args:
#             intents (list[dict]): List of intents, each containing a name and examples.
#         """
#         self.intents = intents
#         self.model = self._train_model()
#
#     def _train_model(self):
#         """
#         Trains the intent classification model using provided examples.
#
#         Returns:
#             Pipeline: A scikit-learn pipeline with a vectorizer and a classifier.
#         """
#         texts = []
#         labels = []
#
#         for intent in self.intents:
#             for example in intent["examples"]:
#                 texts.append(example)
#                 labels.append(intent["name"])
#
#         pipeline = Pipeline([
#             ("vectorizer", CountVectorizer()),
#             ("classifier", MultinomialNB())
#         ])
#
#         return pipeline.fit(texts, labels)
#
#     def predict_intent(self, text: str) -> str:
#         """
#         Predicts the intent for a given text.
#
#         Args:
#             text (str): The input text to classify.
#
#         Returns:
#             str: The name of the predicted intent.
#         """
#         return self.model.predict([text])[0]
