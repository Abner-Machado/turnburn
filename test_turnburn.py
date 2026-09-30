import io
import json
import os
import tempfile
import unittest

import turnburn as tb


def _line(usage, ts="2026-09-30T16:29:00Z", model="claude-opus-4-8", type_="assistant"):
    return json.dumps({
        "type": type_,
        "timestamp": ts,
        "message": {"model": model, "usage": usage},
    })


def _write_jsonl(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


class ParseTest(unittest.TestCase):
    def test_extracts_usage_only_from_assistant(self):
        lines = [
            json.dumps({"type": "user", "message": {"role": "user"}}),
            _line({"input_tokens": 5, "output_tokens": 10, "cache_read_input_tokens": 100,
                   "cache_creation": {"ephemeral_1h_input_tokens": 20, "ephemeral_5m_input_tokens": 0}}),
        ]
        path = _write_jsonl(lines)
        try:
            s = tb.parse_session(path)
        finally:
            os.remove(path)
        self.assertEqual(len(s.turns), 1)
        t = s.turns[0]
        self.assertEqual(t.input, 5)
        self.assertEqual(t.output, 10)
        self.assertEqual(t.cache_read, 100)
        self.assertEqual(t.cache_1h, 20)
        self.assertEqual(t.cache_5m, 0)

    def test_skips_corrupt_lines(self):
        lines = [
            "{not json",
            "",
            _line({"input_tokens": 1, "output_tokens": 2}),
        ]
        path = _write_jsonl(lines)
        try:
            s = tb.parse_session(path)
        finally:
            os.remove(path)
        self.assertEqual(len(s.turns), 1)

    def test_assistant_without_usage_ignored(self):
        lines = [json.dumps({"type": "assistant", "message": {"model": "x"}})]
        path = _write_jsonl(lines)
        try:
            s = tb.parse_session(path)
        finally:
            os.remove(path)
        self.assertEqual(len(s.turns), 0)


class WeightTest(unittest.TestCase):
    def test_weighted_uses_multipliers(self):
        t = tb.Turn(index=1, timestamp="", model="", cache_read=1000, output=100)
        w = tb.DEFAULT_WEIGHTS
        # cache_read 1000*0.1 = 100 ; output 100*5 = 500 ; total 600
        self.assertAlmostEqual(t.weighted(w), 600.0)

    def test_output_costs_more_than_equal_cache_read(self):
        cache = tb.Turn(index=1, timestamp="", model="", cache_read=1000)
        out = tb.Turn(index=2, timestamp="", model="", output=1000)
        w = tb.DEFAULT_WEIGHTS
        self.assertGreater(out.weighted(w), cache.weighted(w))

    def test_parse_weights_override(self):
        w = tb.parse_weights_arg(["output=8", "cache_read=0.2"])
        self.assertEqual(w["output"], 8.0)
        self.assertEqual(w["cache_read"], 0.2)
        self.assertEqual(w["input"], tb.DEFAULT_WEIGHTS["input"])

    def test_parse_weights_rejects_unknown(self):
        with self.assertRaises(ValueError):
            tb.parse_weights_arg(["banana=1"])

    def test_parse_weights_rejects_malformed(self):
        with self.assertRaises(ValueError):
            tb.parse_weights_arg(["output"])


class TotalsTest(unittest.TestCase):
    def test_totals_sum_across_turns(self):
        s = tb.Session(path="x")
        s.turns = [
            tb.Turn(index=1, timestamp="", model="", cache_read=100, output=10),
            tb.Turn(index=2, timestamp="", model="", cache_read=200, input=5),
        ]
        totals = s.totals()
        self.assertEqual(totals["cache_read"], 300)
        self.assertEqual(totals["output"], 10)
        self.assertEqual(totals["input"], 5)


class RenderTest(unittest.TestCase):
    def _session(self):
        s = tb.Session(path="/tmp/sess.jsonl")
        s.turns = [
            tb.Turn(index=1, timestamp="2026-09-30T16:29:00Z", model="m",
                    cache_read=88541, cache_1h=310, input=2, output=100, thinking=0),
            tb.Turn(index=2, timestamp="2026-09-30T16:30:00Z", model="m",
                    cache_read=28454, cache_1h=60087, input=2, output=264, thinking=146),
        ]
        return s

    def test_summary_mentions_output_and_cache(self):
        out = tb.render_summary(self._session(), tb.DEFAULT_WEIGHTS)
        self.assertIn("cache relido", out)
        self.assertIn("output", out)
        self.assertIn("turno mais caro", out)

    def test_summary_empty_session(self):
        s = tb.Session(path="x")
        out = tb.render_summary(s, tb.DEFAULT_WEIGHTS)
        self.assertIn("nenhum turno", out)

    def test_by_turn_top_limits_rows(self):
        out = tb.render_by_turn(self._session(), tb.DEFAULT_WEIGHTS, top_n=1)
        body = [l for l in out.splitlines() if l.startswith("#")]
        self.assertEqual(len(body), 1)

    def test_json_roundtrip(self):
        out = tb.to_json(self._session(), tb.DEFAULT_WEIGHTS)
        data = json.loads(out)
        self.assertEqual(data["turns"], 2)
        self.assertEqual(data["raw_totals"]["cache_read"], 88541 + 28454)
        self.assertEqual(len(data["by_turn"]), 2)


class CliTest(unittest.TestCase):
    def test_missing_arg_returns_2(self):
        self.assertEqual(tb.main([]), 2)

    def test_missing_file_returns_2(self):
        self.assertEqual(tb.main(["/no/such/file.jsonl"]), 2)

    def test_runs_on_real_shape(self):
        path = _write_jsonl([
            _line({"input_tokens": 2, "output_tokens": 264, "cache_read_input_tokens": 28454,
                   "cache_creation": {"ephemeral_1h_input_tokens": 60087, "ephemeral_5m_input_tokens": 0},
                   "output_tokens_details": {"thinking_tokens": 146}}),
        ])
        try:
            self.assertEqual(tb.main([path]), 0)
            self.assertEqual(tb.main([path, "--json"]), 0)
            self.assertEqual(tb.main([path, "--by-turn"]), 0)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
