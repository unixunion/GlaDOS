import unittest

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.plugin_system.plugin_manager import PluginManager, LLM_FUNCTION_REQUEST


class TestPluginManager(unittest.TestCase):
    manager = PluginManager()

    def setUp(self):
        print("Setting up")

        @self.manager.register(name="add", description="Add two numbers")
        def add(a, b):
            return a + b

        self.multiply_definition = FunctionRequest(
            type="function",
            function=FunctionMetadata(
                description="Multiply two numbers",
                parameters=Parameters(
                    type="object",
                    required=['a', 'b'],
                    properties={
                        'a': ParameterType(type="integer", description="the first number"),
                        'b': ParameterType(type="integer", description="the second number")
                    }

                )
            )
        )

        @self.manager.register(
            llm_function_request=self.multiply_definition
        )
        def multiply(a, b):
            return a * b

    def test_simple_register_and_execute_plugin(self):
        """Test registering and executing a plugin."""
        result = self.manager.execute("add", 2, 3)
        self.assertEqual(result, 5)

    def  test_register_with_function_request(self):
        """Test registering a plugin with function_request metadata."""



        metadata = self.manager.get_plugin_metadata("multiply")
        self.assertEqual(metadata["name"], "multiply")
        self.assertEqual(metadata["description"], "Multiply two numbers")
        self.assertEqual(metadata[LLM_FUNCTION_REQUEST], self.multiply_definition.to_dict())

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

    def test_should_process_plugin_output(self):
        definition = FunctionRequest(
            type="function",
            function=FunctionMetadata(
                description="Square of a two numbers",
                parameters=Parameters(
                    type="object",
                    required=['a', 'b'],
                    properties={
                        'a': ParameterType(type="integer", description="the first number"),
                        'b': ParameterType(type="integer", description="the second number")
                    }

                )
            )
        )

        @self.manager.register(llm_function_request=definition, process_output=True)
        def square(x):
            return x * x

        result = self.manager.should_process_plugin_output("square")
        self.assertEqual(result, True)

        @self.manager.register(name="square2", description="Square2 a number",
                               process_output=True)
        def square2(x):
            return x * x

        result = self.manager.should_process_plugin_output("square2")
        self.assertEqual(result, False)

    def test_get_available_plugins(self):
        # this plugin should not be llm callable
        @self.manager.register(name="square", description="Square a number")
        def square(x):
            return x * x

        # this should be llm callable

    def test_get_available_tools(self):
        x = self.manager.get_available_tools()
        self.assertTrue(len(x)>0)
        self.assertIn(self.multiply_definition.to_dict(), x)
        # self.assertEqual(x[1], )


if __name__ == "__main__":
    unittest.main()
