import unittest

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParamaterType
from plugins.plugin_manager import PluginManager


class TestPluginManager(unittest.TestCase):
    manager = PluginManager()

    def setUp(self):
        print("Setting up")

    def test_register_and_execute_plugin(self):
        """Test registering and executing a plugin."""

        @self.manager.register(name="add", description="Add two numbers")
        def add(a, b):
            return a + b

        result = self.manager.execute("add", 2, 3)
        self.assertEqual(result, 5)

    def test_register_with_function_request(self):
        """Test registering a plugin with function_request metadata."""

        multiply_definition = FunctionRequest(
            type="function",
            function=FunctionMetadata(
                name="multiply",
                description="multiply two numbers",
                parameters=Parameters(
                    type="object",
                    required=['a', 'b'],
                    properties={
                        'a': ParamaterType(type="integer", description="the first number"),
                        'b': ParamaterType(type="integer", description="the second number")
                    }

                )
            )
        )

        @self.manager.register(
            name="multiply",
            description="Multiply two numbers",
            function_request=multiply_definition.to_dict()
        )
        def multiply(a, b):
            return a * b

        metadata = self.manager.get_plugin_metadata("multiply")
        self.assertEqual(metadata["name"], "multiply")
        self.assertEqual(metadata["description"], "Multiply two numbers")
        self.assertEqual(metadata["function_request"], multiply_definition.to_dict())

        result = self.manager.execute("multiply", 4, 5)
        self.assertEqual(result, 20)

    def test_plugin_not_found(self):
        """Test executing a plugin that does not exist."""
        with self.assertRaises(ValueError) as context:
            self.manager.execute("non_existent_plugin")

        self.assertIn("Plugin 'non_existent_plugin' not found", str(context.exception))

    def test_list_plugins(self):
        """Test listing all registered plugins."""

        @self.manager.register(name="subtract", description="Subtract two numbers")
        def subtract(a, b):
            return a - b

        plugins = self.manager.list_plugins()
        self.assertIn("subtract", plugins)
        self.assertEqual(plugins["subtract"]["description"], "Subtract two numbers")

    def test_execute_plugin_and_wait(self):
        """Test executing a plugin synchronously and waiting for its result."""

        @self.manager.register(name="square", description="Square a number", function_request={"a": "b"})
        def square(x):
            return x * x

        result = self.manager.execute_plugin_and_wait("square", args=(3,))
        self.assertEqual(result, 9)

    def test_get_available_plugins(self):
        x = self.manager.get_available_plugins()
        self.assertTrue(callable(x['square']))

    def test_get_available_plugins_as_list(self):
        x = self.manager.get_available_plugins_as_list()
        self.assertEqual(x, [{'a': 'b'}])


if __name__ == "__main__":
    unittest.main()
