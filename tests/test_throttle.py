import unittest

from gloss.throttle import DailyQuotaExceeded, Throttle, is_daily_quota, retry_hint


class FakeClock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


class TestThrottle(unittest.TestCase):
    def make(self, **kw):
        fc = FakeClock()
        return Throttle(clock=fc.clock, sleep=fc.sleep, **kw), fc

    def test_calls_are_spaced(self):
        t, fc = self.make(min_interval=4.5)
        for _ in range(3):
            t.call(lambda: 1)
        self.assertEqual(fc.slept, [4.5, 4.5])

    def test_a_per_minute_limit_is_waited_out_using_the_server_hint(self):
        t, fc = self.make(min_interval=0)
        seen = []

        def fn():
            seen.append(1)
            if len(seen) == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED ... GenerateRequestsPerMinutePerProjectPerModel-FreeTier ... Please retry in 9.3s.")
            return "ok"

        self.assertEqual(t.call(fn), "ok")
        self.assertEqual((t.retries, fc.slept), (1, [10.3]))

    def test_a_daily_quota_stops_the_run_instead_of_retrying(self):
        t, _ = self.make(min_interval=0)

        def fn():
            raise RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier")

        with self.assertRaises(DailyQuotaExceeded):
            t.call(fn)

    def test_other_errors_pass_through_and_retries_are_bounded(self):
        t, _ = self.make(min_interval=0, max_retries=2)
        with self.assertRaises(ValueError):
            t.call(lambda: (_ for _ in ()).throw(ValueError("bad request")))
        n = []

        def always():
            n.append(1)
            raise RuntimeError("429 RESOURCE_EXHAUSTED per minute")

        with self.assertRaises(RuntimeError):
            t.call(always)
        self.assertEqual(len(n), 3)

    def test_helpers(self):
        self.assertEqual(retry_hint("Please retry in 9.303375146s."), 9.303375146)
        self.assertEqual(retry_hint("'retryDelay': '9s'"), 9.0)
        self.assertIsNone(retry_hint("nothing"))
        self.assertTrue(is_daily_quota("limit per day exceeded"))
        self.assertFalse(is_daily_quota("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))


if __name__ == "__main__":
    unittest.main()
