"""Exercises the agent loop against a scripted fake client (no API calls)."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace as NS
from unittest import mock

from recon import agent
from tests.test_recon import make_run


def tool_use(i, name, **inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


def reply(blocks, stop="tool_use"):
    return NS(content=blocks, stop_reason=stop, model=agent.MODEL,
              usage=NS(input_tokens=100, output_tokens=50))


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot; loop mutates it
        return self.script.pop(0)


class AgentLoopTests(unittest.TestCase):
    def test_loop_dispatches_tools_reports_errors_and_nudges(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            ids = [e["id"] for e in state.exceptions]
            first, rest = ids[0], ids[1:]
            script = [
                reply([tool_use(0, "get_overview"), tool_use(1, "get_exception", exception_id="EX-999")]),
                reply([tool_use(2, "propose_resolution", exception_id=first, action="correct_ledger_amount",
                                confidence="high", rationale="Check 1043: L-006 vs S-007.", suggested_entry="")]),
                reply([NS(type="text", text="done")], stop="end_turn"),  # quits early -> nudged
                reply([tool_use(3 + n, "propose_resolution", exception_id=x, action="investigate_further",
                                confidence="low", rationale="Needs a look.", suggested_entry="")
                       for n, x in enumerate(rest)]
                      + [tool_use(99, "submit_summary", markdown="Summary.")]),
                reply([NS(type="text", text="done")], stop="end_turn"),
            ]
            fake = FakeClient(script)
            with mock.patch("anthropic.Anthropic", return_value=fake), redirect_stdout(io.StringIO()):
                meta = agent.run_agent(state)

            # bad exception id came back to the model as an error, not a crash
            results = fake.calls[1]["messages"][-1]["content"]
            self.assertTrue(results[1]["is_error"])
            self.assertIn("unknown exception_id", results[1]["content"])
            self.assertEqual(json.loads(results[0]["content"])["matched_pairs"], 15)
            # nudge was sent after the premature end_turn
            self.assertIn("Not finished yet", fake.calls[3]["messages"][-1]["content"])
            # every exception has a pending proposal and the summary is a draft, not approved
            self.assertTrue(all(e["ai_proposal"]["status"] == "PENDING_REVIEW" for e in state.exceptions))
            self.assertEqual(state.data["summary"]["draft"], "Summary.")
            self.assertIsNone(state.data["summary"]["approved"])
            self.assertEqual(meta["tool_calls"], 3 + len(rest) + 1)
            # request shape
            req = fake.calls[0]
            self.assertEqual(req["model"], "claude-opus-5-5")
            self.assertEqual(req["thinking"], {"type": "adaptive"})
            self.assertEqual(req["fallbacks"], "default")

    def test_refusal_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            fake = FakeClient([reply([], stop="refusal")])
            with mock.patch("anthropic.Anthropic", return_value=fake):
                with self.assertRaises(agent.AgentError):
                    agent.run_agent(state, verbose=False)


if __name__ == "__main__":
    unittest.main()
