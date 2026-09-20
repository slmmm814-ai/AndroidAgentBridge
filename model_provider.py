"""
Provider-neutral model interface.

A real provider such as OpenAI, Claude, Gemini, or a local
model can implement this interface later.
"""


class ModelProvider:
    """
    Base interface for AI model providers.
    """

    def generate(self, context):
        """
        Receive the complete agent context and return
        one planner action as a dictionary.
        """
        raise NotImplementedError(
            "ModelProvider.generate() must be implemented"
        )


class MockModelProvider(ModelProvider):
    """
    Local test provider.

    This does not contact any external service.
    """

    def __init__(self, action=None):
        self.action = action or {
            "action": "done"
        }

    def generate(self, context):
        """
        Return a predefined action.
        """
        return dict(self.action)
