"""
Generic AI Planner Adapter.

This module deliberately contains no provider-specific API code.

Flow:
    goal/state/memory
        ->
    planner context
        ->
    external AI adapter
        ->
    one normalized action
"""

from planner import (
    prepare_ai_planner_input,
    plan_from_ai_result,
    plan_next,
)


class AIPlanner:
    """
    Provider-independent AI planner.

    A future provider can implement:
        model_callable(context)

    The callable must return a Python dictionary representing
    exactly one agent action.
    """

    def __init__(
        self,
        model_callable=None,
        model_provider=None,
    ):
        self.model_callable = model_callable
        self.model_provider = model_provider

    def build_context(self, goal, state, memory):
        return prepare_ai_planner_input(
            goal,
            state,
            memory,
        )

    def ask_model(self, context):
        """
        Call the external model adapter.

        No model is configured yet, so this returns None.
        """
        if self.model_provider is not None:
            try:
                result = self.model_provider.generate(context)
            except Exception as e:
                print(
                    f"MODEL PROVIDER ERROR: {e}"
                )
                result = None

            if isinstance(result, dict):
                return result

        if self.model_callable is None:
            return None

        result = self.model_callable(context)

        if not isinstance(result, dict):
            return None

        return result

    def plan(self, goal, state, memory):
        """
        Produce one validated action.

        If an external model is unavailable or returns an invalid
        action, fall back to the existing rule planner.
        """
        context = self.build_context(
            goal,
            state,
            memory,
        )

        ai_result = self.ask_model(context)

        if ai_result is not None:
            action = plan_from_ai_result(ai_result)

            if action is not None:
                return action

        return plan_next(
            goal,
            state,
            memory,
        )


def create_planner(
    model_callable=None,
    model_provider=None,
):
    """
    Factory used by the agent loop.

    Supports both the legacy callable interface and
    the provider interface.
    """
    return AIPlanner(
        model_callable=model_callable,
        model_provider=model_provider,
    )
