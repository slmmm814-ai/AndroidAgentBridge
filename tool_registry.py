"""
Tool Registry for AndroidAgentBridge.

The registry describes the tools available to the AI planner.
Execution remains handled by ToolRouter.
"""

TOOL_DEFINITIONS = {
    "list_files": {
        "description": "List files and directories inside the agent workspace.",
        "arguments": {
            "path": {
                "type": "string",
                "required": False,
                "description": "Relative directory path inside the workspace."
            }
        },
    },

    "read_file": {
        "description": "Read a text file from the agent workspace.",
        "arguments": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Relative file path inside the workspace."
            }
        },
    },

    "make_directory": {
        "description": "Create a directory inside the agent workspace.",
        "arguments": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Relative directory path inside the workspace."
            }
        },
    },

    "write_file": {
        "description": "Create or replace a text file inside the agent workspace.",
        "arguments": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Relative file path inside the workspace."
            },
            "content": {
                "type": "string",
                "required": True,
                "description": "Text content to write."
            }
        },
    },

    "build_project": {
        "description": "Build an Android/Gradle project inside the agent workspace. Only the standard debug APK build is allowed.",
        "arguments": {
            "project_path": {
                "type": "string",
                "required": True,
                "description": "Relative project directory inside the workspace."
            },
            "timeout_seconds": {
                "type": "integer",
                "required": False,
                "description": "Maximum build time in seconds."
            }
        },
    },

    "test_project": {
        "description": "Run the standard Gradle test task for an Android/Gradle project inside the agent workspace.",
        "arguments": {
            "project_path": {
                "type": "string",
                "required": True,
                "description": "Relative project directory inside the workspace."
            },
            "timeout_seconds": {
                "type": "integer",
                "required": False,
                "description": "Maximum test time in seconds."
            }
        },
    },
}


def get_tool_definition(tool_name):
    """Return one tool definition or None."""
    return TOOL_DEFINITIONS.get(tool_name)


def list_tools():
    """Return the names of all registered tools."""
    return list(TOOL_DEFINITIONS.keys())


def validate_tool_call(tool_name, arguments):
    """
    Validate an AI tool request before execution.

    Returns:
        (True, None) when valid
        (False, error_message) when invalid
    """
    definition = get_tool_definition(tool_name)

    if definition is None:
        return False, f"unknown_tool: {tool_name}"

    if not isinstance(arguments, dict):
        return False, "arguments must be an object"

    schema = definition.get("arguments", {})

    for name, spec in schema.items():
        required = spec.get("required", False)

        if required and name not in arguments:
            return False, f"missing required argument: {name}"

        if name not in arguments:
            continue

        value = arguments[name]
        expected_type = spec.get("type")

        if expected_type == "string" and not isinstance(value, str):
            return False, f"argument '{name}' must be a string"

    return True, None


def list_tools_for_ai():
    """
    Return tool definitions in a provider-neutral format
    suitable for an AI planner.
    """
    tools = []

    for name, definition in TOOL_DEFINITIONS.items():
        tools.append({
            "name": name,
            "description": definition.get("description", ""),
            "arguments": definition.get("arguments", {}),
        })

    return tools
