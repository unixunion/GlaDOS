from loguru import logger
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline


class IntentClassifier:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(IntentClassifier, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        logger.info("Initializing IntentClassifier as a singleton instance")
        self.intents = []
        self.model = None
        self._initialized = True

    def add_intent(self, name: str, examples: list):
        """Adds a single intent with its examples."""
        self.intents.append({"name": name, "examples": examples})

    def retrain(self):
        """Retrains the Naive Bayes classifier with the current intents."""
        if not self.intents:
            self.model = None
            logger.warning("No intents available for training. Model not trained.")
            return

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

        self.model = pipeline.fit(texts, labels)
        logger.info("Model retrained with updated intents.")

    def predict_intent(self, text: str) -> (str, float):
        """Predicts the intent for a given text, along with the confidence score."""
        if not self.model:
            raise ValueError("The model has not been trained. Cannot predict intents.")

        probabilities = self.model.predict_proba([text])[0]
        max_index = probabilities.argmax()
        intent = self.model.classes_[max_index]
        confidence = probabilities[max_index]
        return intent, confidence

    def add_intents(self, new_intents: list[dict]):
        """Adds multiple intents to the existing model and retrains it."""
        for intent in new_intents:
            if "name" in intent and "examples" in intent:
                self.intents.append(intent)
            else:
                raise ValueError("Each intent must have a 'name' and 'examples' key.")
        self.retrain()

    # def evaluate(self, test_size: float = 0.2):
    #     """Evaluates the model's performance on a holdout test set."""
    #     if not self.intents:
    #         raise ValueError("No intents available for evaluation.")
    #
    #     texts = []
    #     labels = []
    #
    #     for intent in self.intents:
    #         for example in intent["examples"]:
    #             texts.append(example)
    #             labels.append(intent["name"])
    #
    #     # Split data into training and testing sets
    #     X_train, X_test, y_train, y_test = train_test_split(texts, labels, test_size=test_size, random_state=42)
    #
    #     # Train a new pipeline for evaluation purposes
    #     pipeline = Pipeline([
    #         ("vectorizer", CountVectorizer()),
    #         ("classifier", MultinomialNB())
    #     ])
    #     pipeline.fit(X_train, y_train)
    #
    #     # Make predictions and evaluate
    #     predictions = pipeline.predict(X_test)
    #     accuracy = accuracy_score(y_test, predictions)
    #     report = classification_report(y_test, predictions, output_dict=True)
    #
    #     return {"accuracy": accuracy, "classification_report": report}

    @classmethod
    def get_instance(cls):
        """Provides a singleton instance of IntentClassifier."""
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

